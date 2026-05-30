from __future__ import annotations

import json
from typing import Any

import pytest

from server.shared.inference.backends.lm_studio import (
    LMStudioBackend,
    chat_completions_url,
    lmstudio_queue_key,
    parse_sse_content,
)


class FakeResponse:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self) -> Any:
        for line in self.lines:
            yield line


class FakeClient:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.requests: list[dict[str, Any]] = []

    async def __aenter__(self) -> FakeClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def stream(self, method: str, url: str, json: dict[str, Any]) -> FakeResponse:
        self.requests.append({"method": method, "url": url, "json": json})
        return FakeResponse(self.lines)


@pytest.mark.unit
def test_chat_completions_url_accepts_base_url_or_v1_url() -> None:
    assert (
        chat_completions_url("http://192.168.11.66:1234")
        == "http://192.168.11.66:1234/v1/chat/completions"
    )
    assert (
        chat_completions_url("http://192.168.11.66:1234/v1")
        == "http://192.168.11.66:1234/v1/chat/completions"
    )


@pytest.mark.unit
def test_parse_sse_content_extracts_openai_delta_content() -> None:
    line = 'data: {"choices":[{"delta":{"content":"こんにちは"}}]}'

    assert parse_sse_content(line) == "こんにちは"
    assert parse_sse_content("data: [DONE]") is None
    assert parse_sse_content('data: {"choices":[{"delta":{"role":"assistant"}}]}') is None


@pytest.mark.unit
def test_parse_sse_content_raises_lm_studio_error_payload() -> None:
    line = (
        'data: {"error":{"message":"context length exceeded"},'
        '"message":"provide a shorter input"}'
    )

    with pytest.raises(RuntimeError, match="context length exceeded"):
        parse_sse_content(line)


@pytest.mark.unit
def test_lm_studio_queue_key_allows_different_models_to_run_independently() -> None:
    assert (
        lmstudio_queue_key("http://192.168.11.66:1234", "gemma-4-26b-a4b-it-mlx")
        == "lmstudio:http://192.168.11.66:1234:gemma-4-26b-a4b-it-mlx"
    )
    assert lmstudio_queue_key(
        "http://192.168.11.66:1234",
        "gemma-4-26b-a4b-it-mlx",
    ) != lmstudio_queue_key(
        "http://192.168.11.66:1234",
        "gemma-4-e2b-it-mlx",
    )


@pytest.mark.unit
async def test_lm_studio_backend_streams_openai_compatible_sse() -> None:
    fake_client = FakeClient(
        [
            'data: {"choices":[{"delta":{"role":"assistant","content":"こん"}}]}',
            'data: {"choices":[{"delta":{"content":"にちは"}}]}',
            "data: [DONE]",
        ]
    )
    backend = LMStudioBackend(
        name="lmstudio_gemma4_e2b",
        url="http://192.168.11.66:1234",
        model="gemma-4-e2b-it-mlx",
        client_factory=lambda: fake_client,
    )

    chunks = [
        chunk
        async for chunk in backend.chat_stream(
            "日本語だけで返して。",
            [{"role": "user", "content": "挨拶して。"}],
        )
    ]

    assert chunks == ["こん", "にちは"]
    assert fake_client.requests == [
        {
            "method": "POST",
            "url": "http://192.168.11.66:1234/v1/chat/completions",
            "json": {
                "model": "gemma-4-e2b-it-mlx",
                "messages": [
                    {"role": "system", "content": "日本語だけで返して。"},
                    {"role": "user", "content": "挨拶して。"},
                ],
                "stream": True,
                "max_tokens": 180,
                "temperature": 0.0,
            },
        }
    ]


@pytest.mark.unit
async def test_lm_studio_backend_can_request_structured_output() -> None:
    fake_client = FakeClient(
        [
            'data: {"choices":[{"delta":{"content":"{\\"items\\":[]}"}}]}',
            "data: [DONE]",
        ]
    )
    backend = LMStudioBackend(
        name="lmstudio_gemma4_e2b",
        url="http://192.168.11.66:1234",
        model="gemma-4-e2b-it-mlx",
        client_factory=lambda: fake_client,
    )
    schema = {
        "name": "unit_schema",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"items": {"type": "array"}},
            "required": ["items"],
        },
    }

    chunks = [
        chunk
        async for chunk in backend.chat_stream_structured(
            "JSONで返して。",
            [{"role": "user", "content": "空で。"}],
            json_schema=schema,
            max_tokens=512,
        )
    ]

    assert chunks == ['{"items":[]}']
    assert fake_client.requests[0]["json"]["response_format"] == {
        "type": "json_schema",
        "json_schema": schema,
    }
    assert fake_client.requests[0]["json"]["max_tokens"] == 512


@pytest.mark.unit
async def test_lm_studio_backend_writes_jsonl_lifecycle_trace(tmp_path, monkeypatch) -> None:
    trace_path = tmp_path / "backend-trace.jsonl"
    monkeypatch.setenv("TOMOKO_BACKEND_TRACE_FILE", str(trace_path))
    fake_client = FakeClient(
        [
            'data: {"choices":[{"delta":{"content":"こ"}}]}',
            'data: {"choices":[{"delta":{"content":"ん"}}]}',
            "data: [DONE]",
        ]
    )
    backend = LMStudioBackend(
        name="lmstudio_gemma4_e4b",
        url="http://192.168.11.66:1234",
        model="gemma-4-e4b-it-mlx",
        client_factory=lambda: fake_client,
    )

    chunks = [
        chunk
        async for chunk in backend.chat_stream(
            "日本語だけで返して。",
            [{"role": "user", "content": "挨拶して。"}],
            trace_role="conversation",
        )
    ]

    rows = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert chunks == ["こ", "ん"]
    assert [row["event"] for row in rows] == [
        "start",
        "queue_acquired",
        "response_headers",
        "first_delta",
        "done",
    ]
    assert {row["trace"] for row in rows} == {"tomoko_backend_call"}
    assert {row["role"] for row in rows} == {"conversation"}
    assert {row["backend"] for row in rows} == {"lmstudio_gemma4_e4b"}
    assert {row["model"] for row in rows} == {"gemma-4-e4b-it-mlx"}
    assert (
        rows[1]["queue_key"]
        == "lmstudio:http://192.168.11.66:1234:gemma-4-e4b-it-mlx"
    )
    assert rows[-1]["chunk_count"] == 2
    assert len({row["request_id"] for row in rows}) == 1


@pytest.mark.unit
async def test_lm_studio_backend_writes_error_trace(tmp_path, monkeypatch) -> None:
    trace_path = tmp_path / "backend-trace.jsonl"
    monkeypatch.setenv("TOMOKO_BACKEND_TRACE_FILE", str(trace_path))

    class FailingClient(FakeClient):
        def stream(self, method: str, url: str, json: dict[str, Any]) -> FakeResponse:
            del method, url, json
            raise RuntimeError("boom")

    backend = LMStudioBackend(
        name="lmstudio_gemma4_e4b",
        url="http://192.168.11.66:1234",
        model="gemma-4-e4b-it-mlx",
        client_factory=lambda: FailingClient([]),
    )

    with pytest.raises(RuntimeError, match="boom"):
        _ = [
            chunk
            async for chunk in backend.chat_stream(
                "日本語だけで返して。",
                [{"role": "user", "content": "挨拶して。"}],
                trace_role="conversation",
            )
        ]

    rows = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert [row["event"] for row in rows] == ["start", "queue_acquired", "error"]
    assert rows[-1]["error"] == "RuntimeError"
