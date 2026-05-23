from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import httpx

from server.shared.inference.backends.base import InferenceBackend

logger = logging.getLogger(__name__)


def chat_completions_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        return f"{normalized}/chat/completions"
    return f"{normalized}/v1/chat/completions"


def parse_sse_content(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith("data:"):
        return None

    data = stripped.removeprefix("data:").strip()
    if not data or data == "[DONE]":
        return None

    payload = json.loads(data)
    choices = payload.get("choices") or []
    if not choices:
        return None

    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    if not isinstance(content, str) or content == "":
        return None
    return content


class LMStudioBackend(InferenceBackend):
    def __init__(
        self,
        *,
        name: str,
        url: str,
        model: str,
        privacy_allowed: bool = True,
        timeout_sec: float = 60.0,
        max_tokens: int = 180,
        client_factory: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
    ) -> None:
        self.name = name
        self.url = url.rstrip("/")
        self.model = model
        self.privacy_allowed = privacy_allowed
        self.timeout_sec = timeout_sec
        self.max_tokens = max_tokens
        self._client_factory = client_factory

    async def warm_up(self) -> None:
        started_at = time.perf_counter()
        async for _ in self.chat_stream(
            "あなたはTTSでそのまま読める自然な日本語だけで短く答えます。",
            [{"role": "user", "content": "短く返事して。"}],
        ):
            pass
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        logger.info(
            "LM Studio backend warmed up name=%s model=%s elapsed_ms=%.1f",
            self.name,
            self.model,
            elapsed_ms,
        )

    async def chat_stream(
        self, system_prompt: str, messages: list[dict[str, str]]
    ) -> AsyncGenerator[str, None]:
        formatted_messages = [{"role": "system", "content": system_prompt}] + messages
        payload = {
            "model": self.model,
            "messages": formatted_messages,
            "stream": True,
            "max_tokens": self.max_tokens,
            "temperature": 0.0,
        }

        async with self._create_client() as client:
            async with client.stream(
                "POST",
                chat_completions_url(self.url),
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    try:
                        content = parse_sse_content(line)
                    except json.JSONDecodeError:
                        logger.warning("Invalid LM Studio SSE line ignored: %r", line)
                        continue
                    if content is not None:
                        yield content

    def _create_client(self) -> AbstractAsyncContextManager[Any]:
        if self._client_factory is not None:
            return self._client_factory()
        timeout = httpx.Timeout(self.timeout_sec, connect=5.0)
        return httpx.AsyncClient(timeout=timeout)
