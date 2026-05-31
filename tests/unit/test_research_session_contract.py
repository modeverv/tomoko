from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import Mock

import numpy as np
import pytest

from server.edge.pipeline.vad import VADProcessor
from server.gateway.research import (
    ResearchCitation,
    ResearchCommandRunner,
    ResearchRequest,
    ResearchResult,
    ResearchResultSummarizer,
)
from server.session import TomoroSession
from server.shared.models import ConnectedOutputState, SessionEvent, ThinkingEvent, Transcript
from server.shared.research_results import InMemoryResearchResultStore


class QuietVAD:
    def process_chunk(self, chunk: np.ndarray) -> float:
        del chunk
        return 0.0


class FakeResearchClient:
    def __init__(self, result: ResearchResult) -> None:
        self.result = result
        self.requests: list[ResearchRequest] = []

    async def search(self, request: ResearchRequest) -> ResearchResult:
        self.requests.append(request)
        return self.result


class FakeSummaryBackend:
    name = "fake_summary"
    privacy_allowed = True

    def __init__(self) -> None:
        self.prompts: list[tuple[str, list[dict[str, str]]]] = []

    async def chat_stream(self, system_prompt: str, messages: list[dict[str, str]]):
        self.prompts.append((system_prompt, messages))
        yield "OpenAI調査のLLM要約です。"


class FakeEmbeddingBackend:
    async def embed_passage(self, text: str) -> list[float]:
        assert text == "OpenAI調査のLLM要約です。"
        return [1.0, 0.0, 0.0]


class FakeConversationBackend:
    name = "fake_conversation"
    privacy_allowed = True


class FakeRouter:
    async def select(self, role: str, preference: str = "latency") -> FakeConversationBackend:
        del role, preference
        return FakeConversationBackend()


class FakeThinkingMode:
    def __init__(self) -> None:
        self.response_directives: list[str | None] = []

    async def think(self, backend, thinking_input):
        del backend
        self.response_directives.append(thinking_input.response_directive)
        yield ThinkingEvent(type="emotion", value="thinking")
        yield ThinkingEvent(type="text_delta", value="調べてみるね。少し待って。")
        yield ThinkingEvent(type="done", value="")


def _session(
    events: list[dict[str, object]] | None = None,
    **kwargs,
) -> TomoroSession:
    if events is None:
        events = []
    return TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=events.append,
        connected_output_state=ConnectedOutputState.single_client(device_id="desk"),
        **kwargs,
    )


def _transcript(text: str) -> Transcript:
    return Transcript(
        text=text,
        device_id="desk",
        speaker=None,
        audio_level_db=-20.0,
        recorded_at=datetime(2026, 5, 31, tzinfo=UTC),
        is_final=True,
    )


@pytest.mark.unit
async def test_research_requested_emits_submit_command() -> None:
    session = _session()
    request = ResearchRequest(query="OpenAI news", mode="quick", locale="ja-JP")

    result = await session.post_event(
        SessionEvent(type="research_requested", payload={"request": request})
    )

    assert result.emissions[0].type == "research_request_accepted"
    assert result.emissions[0].payload["query"] == "OpenAI news"
    assert [command.type for command in result.commands] == ["submit_research_request"]
    assert result.commands[0].payload["request"] == request
    assert str(result.commands[0].payload["request_id"]).startswith("research-")


@pytest.mark.unit
async def test_research_command_runner_posts_result_ready_event() -> None:
    events: list[dict[str, object]] = []
    session = _session(events)
    request = ResearchRequest(query="OpenAI news")
    client = FakeResearchClient(
        ResearchResult(
            status="completed",
            query="OpenAI news",
            short_answer="新情報はありません。",
            citations=(ResearchCitation(title="OpenAI", url="https://openai.com/news/"),),
            fetched_at=datetime(2026, 5, 31, tzinfo=UTC),
        )
    )
    runner = ResearchCommandRunner(session=session, client=client)
    accepted = await session.post_event(
        SessionEvent(type="research_requested", payload={"request": request})
    )

    await runner.run_result(accepted)

    assert client.requests == [request]
    assert events[0]["type"] == "research_request_accepted"
    assert events[1]["type"] == "research_result_ready"
    assert events[1]["status"] == "completed"
    assert events[1]["speakable"] is True
    assert events[1]["notice_text"] == "調べ終わったよ。結果を教えてって言ってね。"
    reply_event = next(event for event in events if event["type"] == "reply_text")
    assert reply_event["delta"] == "調べ終わったよ。結果を教えてって言ってね。"


@pytest.mark.unit
async def test_research_command_runner_ingests_llm_summary_embedding() -> None:
    session = _session()
    request = ResearchRequest(query="OpenAI news")
    client = FakeResearchClient(
        ResearchResult(
            status="completed",
            query="OpenAI news",
            short_answer="OpenAIの短い調査結果です。",
            citations=(ResearchCitation(title="OpenAI", url="https://openai.com/news/"),),
            fetched_at=datetime(2026, 5, 31, tzinfo=UTC),
            provider_trace_id="trace-openai",
        )
    )
    summary_backend = FakeSummaryBackend()
    store = InMemoryResearchResultStore()
    runner = ResearchCommandRunner(
        session=session,
        client=client,
        result_store=store,
        embedding_backend=FakeEmbeddingBackend(),
        summarizer=ResearchResultSummarizer(backend=summary_backend),
    )
    accepted = await session.post_event(
        SessionEvent(type="research_requested", payload={"request": request})
    )

    await runner.run_result(accepted)

    assert summary_backend.prompts
    assert len(store.rows) == 1
    row = store.rows[0]
    assert row.result_id == "trace-openai"
    assert row.summary_text == "OpenAI調査のLLM要約です。"
    assert row.embedding == [1.0, 0.0, 0.0]
    assert row.short_answer == "OpenAIの短い調査結果です。"


@pytest.mark.unit
async def test_research_command_runner_logs_ingestion_lifecycle(monkeypatch) -> None:
    session = _session()
    request = ResearchRequest(query="OpenAI news")
    client = FakeResearchClient(
        ResearchResult(
            status="completed",
            query="OpenAI news",
            short_answer="OpenAIの短い調査結果です。",
            fetched_at=datetime(2026, 5, 31, tzinfo=UTC),
            provider_trace_id="trace-openai",
        )
    )
    store = InMemoryResearchResultStore()
    runner = ResearchCommandRunner(
        session=session,
        client=client,
        result_store=store,
        embedding_backend=FakeEmbeddingBackend(),
        summarizer=ResearchResultSummarizer(backend=FakeSummaryBackend()),
    )
    accepted = await session.post_event(
        SessionEvent(type="research_requested", payload={"request": request})
    )

    info = Mock()
    monkeypatch.setattr("server.gateway.research.logger.info", info)
    await runner.run_result(accepted)

    messages = [call.args[0] for call in info.call_args_list]
    assert any("Research command runner starting request" in message for message in messages)
    assert any("Research command runner ingested result" in message for message in messages)
    assert any("trace-openai" in call.args for call in info.call_args_list)


@pytest.mark.unit
async def test_process_transcript_routes_research_request_before_normal_reply() -> None:
    events: list[dict[str, object]] = []
    session = _session(events)

    await session.process_transcript(_transcript("智子オバマ大統領について調べて"))

    event_types = [str(event["type"]) for event in events]
    assert "research_request_accepted" in event_types
    accepted = next(event for event in events if event["type"] == "research_request_accepted")
    assert accepted["query"] == "オバマ大統領について"
    assert "reply_text" not in event_types


@pytest.mark.unit
async def test_process_transcript_hands_research_command_to_background_handler() -> None:
    session = _session()
    called = asyncio.Event()
    results = []

    async def handler(result):
        results.append(result)
        called.set()

    session.set_research_transition_handler(handler)

    await session.process_transcript(_transcript("OpenAIについて調べて"))
    await asyncio.wait_for(called.wait(), timeout=1.0)

    assert results
    assert results[0].emissions[0].type == "research_request_accepted"
    assert results[0].commands[0].type == "submit_research_request"
    assert results[0].commands[0].payload["request"].query == "OpenAIについて"


@pytest.mark.unit
async def test_process_transcript_logs_research_background_task_lifecycle(monkeypatch) -> None:
    session = _session()
    called = asyncio.Event()

    async def handler(result):
        del result
        called.set()

    info = Mock()
    monkeypatch.setattr("server.session.logger.info", info)
    session.set_research_transition_handler(handler)

    await session.process_transcript(_transcript("OpenAIについて調べて"))
    await asyncio.wait_for(called.wait(), timeout=1.0)
    await asyncio.sleep(0)

    messages = [call.args[0] for call in info.call_args_list]
    assert any(
        "TomoroSession scheduling research transition handler" in message
        for message in messages
    )
    assert any("TomoroSession research transition task finished" in message for message in messages)


@pytest.mark.unit
async def test_process_transcript_starts_llm_wait_reply_for_research_request() -> None:
    events: list[dict[str, object]] = []
    thinking_mode = FakeThinkingMode()
    session = _session(
        events,
        router=FakeRouter(),
        thinking_mode=thinking_mode,
    )

    await session.process_transcript(_transcript("OpenAIについて調べて"))
    await session._wait_for_reply_task()

    assert thinking_mode.response_directives
    directive = thinking_mode.response_directives[0]
    assert directive is not None
    assert "今は調査結果を答えず" in directive
    assert "調査クエリ: OpenAIについて" in directive
    event_types = [event["type"] for event in events]
    assert "research_request_accepted" in event_types
    assert "reply_text" in event_types


@pytest.mark.unit
async def test_research_result_ready_failure_starts_failure_notice_reply() -> None:
    session = _session()

    result = await session.post_event(
        SessionEvent(
            type="research_result_ready",
            payload={
                "request_id": "research-1",
                "result": ResearchResult(
                    status="needs_human",
                    query="OpenAI",
                    error_reason="login required",
                ),
            },
        )
    )

    assert result.emissions[0].type == "research_result_ready"
    assert result.emissions[0].payload["status"] == "needs_human"
    assert result.emissions[0].payload["speakable"] is False
    assert result.emissions[0].payload["notice_text"] == "調べきれなかったみたい。"
    assert [command.type for command in result.commands] == [
        "start_research_notice_reply"
    ]
    assert result.commands[0].payload["text"] == "調べきれなかったみたい。"


@pytest.mark.unit
async def test_research_result_ready_completed_starts_notice_reply() -> None:
    session = _session()

    result = await session.post_event(
        SessionEvent(
            type="research_result_ready",
            payload={
                "request_id": "research-1",
                "device_id": "desk",
                "result": ResearchResult(
                    status="completed",
                    query="OpenAI",
                    short_answer="OpenAIの短い調査結果です。",
                ),
            },
        )
    )

    assert result.emissions[0].type == "research_result_ready"
    assert (
        result.emissions[0].payload["notice_text"]
        == "調べ終わったよ。結果を教えてって言ってね。"
    )
    assert [command.type for command in result.commands] == [
        "start_research_notice_reply"
    ]
    assert result.commands[0].payload["text"] == (
        "調べ終わったよ。結果を教えてって言ってね。"
    )
    assert result.commands[0].payload["device_id"] == "desk"
    assert result.commands[0].payload["request_id"] == "research-1"


@pytest.mark.unit
async def test_research_answer_requested_keeps_pending_result_reusable() -> None:
    session = _session()
    result = ResearchResult(
        status="completed",
        query="OpenAI news",
        short_answer="OpenAIの短い調査結果です。",
        citations=(ResearchCitation(title="OpenAI", url="https://openai.com/news/"),),
        fetched_at=datetime(2026, 5, 31, tzinfo=UTC),
    )
    await session.post_event(
        SessionEvent(
            type="research_result_ready",
            payload={"request_id": "research-1", "result": result},
        )
    )

    answer = await session.post_event(
        SessionEvent(
            type="research_answer_requested",
            payload={"transcript": _transcript("教えて")},
        )
    )
    second_answer = await session.post_event(
        SessionEvent(
            type="research_answer_requested",
            payload={"transcript": _transcript("もう一回教えて")},
        )
    )

    assert answer.emissions[0].type == "research_answer_requested"
    assert answer.emissions[0].payload["short_answer"] == "OpenAIの短い調査結果です。"
    assert answer.emissions[0].payload["citation_count"] == 1
    assert [command.type for command in answer.commands] == ["start_research_answer_reply"]
    assert answer.commands[0].payload["text"] == "OpenAIの短い調査結果です。"
    assert answer.commands[0].payload["request_id"] == "research-1"
    assert second_answer.emissions[0].type == "research_answer_requested"
    assert second_answer.commands[0].payload["text"] == "OpenAIの短い調査結果です。"


@pytest.mark.unit
async def test_process_transcript_routes_teach_me_followup_to_research_answer() -> None:
    events: list[dict[str, object]] = []
    session = _session(events)
    await session.post_event(
        SessionEvent(
            type="research_result_ready",
            payload={
                "request_id": "research-1",
                "result": ResearchResult(
                    status="completed",
                    query="OpenAI news",
                    short_answer="OpenAIの短い調査結果です。",
                    fetched_at=datetime(2026, 5, 31, tzinfo=UTC),
                ),
            },
        )
    )

    await session.process_transcript(_transcript("うん、教えて"))

    event_types = [event["type"] for event in events]
    assert "research_answer_requested" in event_types
    assert "reply_text" in event_types
    assert "reply_done" in event_types
    reply_text = next(event for event in events if event["type"] == "reply_text")
    assert reply_text["delta"] == "OpenAIの短い調査結果です。"


@pytest.mark.unit
async def test_process_transcript_routes_result_teach_me_followup_to_research_answer() -> None:
    events: list[dict[str, object]] = []
    session = _session(events)
    await session.post_event(
        SessionEvent(
            type="research_result_ready",
            payload={
                "request_id": "research-1",
                "result": ResearchResult(
                    status="completed",
                    query="OpenAI news",
                    short_answer="OpenAIの短い調査結果です。",
                    fetched_at=datetime(2026, 5, 31, tzinfo=UTC),
                ),
            },
        )
    )

    await session.process_transcript(_transcript("結果を教えて"))

    event_types = [event["type"] for event in events]
    assert "research_answer_requested" in event_types
    reply_text = next(event for event in events if event["type"] == "reply_text")
    assert reply_text["delta"] == "OpenAIの短い調査結果です。"


@pytest.mark.unit
async def test_process_transcript_routes_query_overlap_to_research_answer() -> None:
    events: list[dict[str, object]] = []
    session = _session(events)
    await session.post_event(
        SessionEvent(
            type="research_result_ready",
            payload={
                "request_id": "research-1",
                "result": ResearchResult(
                    status="completed",
                    query="今日のOpenAI関連ニュースを短く",
                    short_answer="OpenAIの短い調査結果です。",
                    fetched_at=datetime(2026, 5, 31, tzinfo=UTC),
                ),
            },
        )
    )

    await session.process_transcript(_transcript("OpenAIについて知ってることある？"))

    event_types = [event["type"] for event in events]
    assert "research_answer_requested" in event_types
    reply_text = next(event for event in events if event["type"] == "reply_text")
    assert reply_text["delta"] == "OpenAIの短い調査結果です。"


@pytest.mark.unit
async def test_process_transcript_ignores_unrelated_query_overlap_request() -> None:
    events: list[dict[str, object]] = []
    session = _session(events)
    await session.post_event(
        SessionEvent(
            type="research_result_ready",
            payload={
                "request_id": "research-1",
                "result": ResearchResult(
                    status="completed",
                    query="今日のOpenAI関連ニュースを短く",
                    short_answer="OpenAIの短い調査結果です。",
                    fetched_at=datetime(2026, 5, 31, tzinfo=UTC),
                ),
            },
        )
    )

    await session.process_transcript(_transcript("Anthropicについて知ってることある？"))

    assert "research_answer_requested" not in [event["type"] for event in events]
