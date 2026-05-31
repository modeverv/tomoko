from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from server.edge.pipeline.vad import VADProcessor
from server.gateway.research import (
    ResearchCitation,
    ResearchCommandRunner,
    ResearchRequest,
    ResearchResult,
)
from server.session import TomoroSession
from server.shared.models import ConnectedOutputState, SessionEvent


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


def _session(events: list[dict[str, object]] | None = None) -> TomoroSession:
    if events is None:
        events = []
    return TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=events.append,
        connected_output_state=ConnectedOutputState.single_client(device_id="desk"),
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
    assert events[1]["notice_text"] == "調べ終わったよ。聞く？"


@pytest.mark.unit
async def test_research_result_ready_failure_does_not_claim_speakable() -> None:
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

    assert result.commands == []
    assert result.emissions[0].type == "research_result_ready"
    assert result.emissions[0].payload["status"] == "needs_human"
    assert result.emissions[0].payload["speakable"] is False
    assert result.emissions[0].payload["notice_text"] == "調べきれなかったみたい。"
