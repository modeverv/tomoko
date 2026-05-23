from __future__ import annotations

from typing import Any

import pytest

from server.shared.inference.backends.lm_studio import (
    LMStudioBackend,
    chat_completions_url,
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
