from __future__ import annotations

import json

import pytest

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
    calls: list[tuple[list[str], str, float]] = []

    async def fake_runner(command: list[str], stdin_text: str, timeout_sec: float) -> str:
        calls.append((command, stdin_text, timeout_sec))
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
def test_research_result_speakable_only_when_completed_with_answer() -> None:
    assert ResearchResult(status="completed", query="x", short_answer="ok").is_speakable()
    assert not ResearchResult(status="timeout", query="x", error_reason="slow").is_speakable()
