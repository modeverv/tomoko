from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from server.edge.main import _create_default_research_mcp_client
from server.gateway.research import (
    ResearchIntentDetector,
    ResearchMcpClient,
    ResearchRequest,
    ResearchResult,
    is_research_answer_request,
    parse_mcp_tool_call_response,
)


@pytest.mark.unit
def test_research_intent_detector_extracts_search_query() -> None:
    detector = ResearchIntentDetector()

    request = detector.detect("ともこ、今日のOpenAI関連ニュースを短く調べて")

    assert request is not None
    assert request.query == "今日のOpenAI関連ニュースを短く"
    assert request.mode == "quick"
    assert request.locale == "ja-JP"


@pytest.mark.unit
def test_research_intent_detector_strips_kanji_wake_name() -> None:
    request = ResearchIntentDetector().detect("智子オバマ大統領について調べて")

    assert request is not None
    assert request.query == "オバマ大統領について"


@pytest.mark.unit
@pytest.mark.parametrize("text", ["なるほどね", "さっきの話なんだけど", "今何時"])
def test_research_intent_detector_ignores_chitchat(text: str) -> None:
    assert ResearchIntentDetector().detect(text) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    ["教えて", "うん、教えて", "聞かせて", "結果を教えて", "はい、お願い"],
)
def test_research_answer_request_detects_followup(text: str) -> None:
    assert is_research_answer_request(text)


@pytest.mark.unit
def test_research_answer_request_can_match_query_overlap() -> None:
    assert is_research_answer_request(
        "OpenAIについて知ってることある？",
        query="今日のOpenAI関連ニュースを短く",
    )
    assert not is_research_answer_request(
        "Anthropicについて知ってることある？",
        query="今日のOpenAI関連ニュースを短く",
    )


@pytest.mark.unit
def test_research_answer_request_requires_overlap_for_topic_answer_cue() -> None:
    assert is_research_answer_request(
        "手書について教えて",
        query="手書についてみて",
    )
    assert not is_research_answer_request(
        "日本の首相について教えて",
        query="手書についてみて",
    )


@pytest.mark.unit
@pytest.mark.parametrize("text", ["なるほどね", "それは違うかも", "今何時"])
def test_research_answer_request_ignores_unrelated_text(text: str) -> None:
    assert not is_research_answer_request(text)


@pytest.mark.unit
def test_research_answer_request_requires_query_for_knowledge_followup() -> None:
    assert not is_research_answer_request("OpenAIについて知ってることある？")


@pytest.mark.unit
def test_parse_mcp_tool_call_response_reads_structured_content_and_dedupes_urls() -> None:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "structuredContent": {
                "status": "completed",
                "query": "OpenAI",
                "provider": "perplexity",
                "short_answer": "新情報はありません。",
                "citations": [
                    {"title": "A", "url": "https://example.com/a", "source": "example.com"},
                    {
                        "title": "A duplicate",
                        "url": "https://example.com/a",
                        "source": "example.com",
                    },
                    {"title": "B", "url": "https://example.com/b", "source": "example.com"},
                ],
                "confidence": 0.7,
                "provider_trace_id": "trace-1",
                "raw_artifact_path": "artifacts/trace-1.json",
                "error_reason": None,
            },
            "isError": False,
        },
    }

    result = parse_mcp_tool_call_response(json.dumps(payload, ensure_ascii=False))

    assert result.status == "completed"
    assert result.short_answer == "新情報はありません。"
    assert [citation.url for citation in result.citations] == [
        "https://example.com/a",
        "https://example.com/b",
    ]


@pytest.mark.unit
def test_parse_mcp_tool_call_response_maps_error_to_failed_result() -> None:
    payload = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "bad"}}

    result = parse_mcp_tool_call_response(json.dumps(payload), fallback_query="OpenAI")

    assert result.status == "failed"
    assert result.query == "OpenAI"
    assert result.error_reason == "bad"


@pytest.mark.unit
async def test_research_mcp_client_builds_json_rpc_tool_call() -> None:
    calls: list[tuple[list[str], str, float, Path | None]] = []

    async def fake_runner(
        command: list[str],
        stdin_text: str,
        timeout_sec: float,
        cwd: Path | None,
    ) -> str:
        calls.append((command, stdin_text, timeout_sec, cwd))
        return json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "structuredContent": {
                        "status": "completed",
                        "query": "OpenAI",
                        "short_answer": "ok",
                        "citations": [],
                    },
                    "isError": False,
                },
            }
        )

    client = ResearchMcpClient(command=("uv", "run", "tomoko-research-mcp"), runner=fake_runner)

    result = await client.search(ResearchRequest(query="OpenAI"))

    assert result.status == "completed"
    assert result.short_answer == "ok"
    assert calls[0][0] == ["uv", "run", "tomoko-research-mcp"]
    request_payload = json.loads(calls[0][1])
    assert request_payload["method"] == "tools/call"
    assert request_payload["params"]["name"] == "research.search"
    assert request_payload["params"]["arguments"]["query"] == "OpenAI"


@pytest.mark.unit
async def test_research_mcp_client_logs_subprocess_lifecycle(monkeypatch) -> None:
    async def fake_runner(
        command: list[str],
        stdin_text: str,
        timeout_sec: float,
        cwd: Path | None,
    ) -> str:
        del command, stdin_text, timeout_sec, cwd
        return json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "structuredContent": {
                        "status": "completed",
                        "query": "OpenAI",
                        "short_answer": "ok",
                        "provider_trace_id": "trace-openai",
                        "citations": [],
                    },
                    "isError": False,
                },
            }
        )

    info = Mock()
    monkeypatch.setattr("server.gateway.research.logger.info", info)
    client = ResearchMcpClient(command=("uv", "run", "tomoko-research-mcp"), runner=fake_runner)

    result = await client.search(ResearchRequest(query="OpenAI"))

    assert result.status == "completed"
    messages = [call.args[0] for call in info.call_args_list]
    assert any("Research MCP subprocess starting" in message for message in messages)
    assert any("Research MCP subprocess completed" in message for message in messages)
    assert any("trace-openai" in call.args for call in info.call_args_list)


@pytest.mark.unit
async def test_research_mcp_client_logs_timeout(monkeypatch) -> None:
    async def fake_runner(
        command: list[str],
        stdin_text: str,
        timeout_sec: float,
        cwd: Path | None,
    ) -> str:
        del command, stdin_text, timeout_sec, cwd
        raise TimeoutError("slow")

    warning = Mock()
    monkeypatch.setattr("server.gateway.research.logger.warning", warning)
    client = ResearchMcpClient(command=("uv", "run", "tomoko-research-mcp"), runner=fake_runner)

    result = await client.search(ResearchRequest(query="OpenAI"))

    assert result.status == "timeout"
    messages = [call.args[0] for call in warning.call_args_list]
    assert any("Research MCP subprocess timed out" in message for message in messages)


@pytest.mark.unit
def test_default_research_mcp_client_points_to_sibling_operator(monkeypatch) -> None:
    monkeypatch.delenv("TOMOKO_RESEARCH_MCP_COMMAND", raising=False)

    client = _create_default_research_mcp_client()

    assert client.command == ("uv", "run", "tomoko-research-mcp")
    assert client.cwd is not None
    operator_dir = client.cwd
    assert operator_dir.name == "tomoko-research-operator"
    assert operator_dir.parent.name == "by-llms"
    assert operator_dir.parent / "tomoko" == Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_research_result_speakable_only_when_completed_with_answer() -> None:
    assert ResearchResult(status="completed", query="x", short_answer="ok").is_speakable()
    assert not ResearchResult(status="timeout", query="x", error_reason="slow").is_speakable()
