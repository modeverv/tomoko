from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any
from uuid import uuid4

import httpx

from server.shared.inference.backends.base import InferenceBackend
from server.shared.inference.trace import trace_backend_call

logger = logging.getLogger(__name__)

_SEMAPHORES: dict[str, asyncio.Semaphore] = {}


def chat_completions_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        return f"{normalized}/chat/completions"
    return f"{normalized}/v1/chat/completions"


def lmstudio_queue_key(base_url: str, model: str) -> str:
    return f"lmstudio:{base_url.rstrip('/')}:{model}"


def parse_sse_content(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith("data:"):
        return None

    data = stripped.removeprefix("data:").strip()
    if not data or data == "[DONE]":
        return None

    payload = json.loads(data)
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message") or payload.get("message") or "LM Studio error"
        raise RuntimeError(str(message))
    if "error" in payload:
        raise RuntimeError(str(payload["error"]))

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
        chat_template_kwargs: dict[str, Any] | None = None,
        client_factory: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
    ) -> None:
        self.name = name
        self.url = url.rstrip("/")
        self.model = model
        self.privacy_allowed = privacy_allowed
        self.timeout_sec = timeout_sec
        self.max_tokens = max_tokens
        self.chat_template_kwargs = dict(chat_template_kwargs or {})
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
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        trace_role: str | None = None,
    ) -> AsyncGenerator[str, None]:
        async for content in self._chat_stream(
            system_prompt,
            messages,
            response_format=None,
            max_tokens=max_tokens or self.max_tokens,
            trace_role=trace_role or "unknown",
        ):
            yield content

    async def chat_stream_structured(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        *,
        json_schema: dict[str, Any],
        max_tokens: int | None = None,
        trace_role: str | None = None,
    ) -> AsyncGenerator[str, None]:
        async for content in self._chat_stream(
            system_prompt,
            messages,
            response_format={
                "type": "json_schema",
                "json_schema": json_schema,
            },
            max_tokens=max_tokens or self.max_tokens,
            trace_role=trace_role or "unknown",
        ):
            yield content

    async def _chat_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, Any] | None,
        max_tokens: int,
        trace_role: str,
    ) -> AsyncGenerator[str, None]:
        request_id = str(uuid4())
        started_at = time.perf_counter()
        queue_key = lmstudio_queue_key(self.url, self.model)
        trace_backend_call(
            event="start",
            kind="llm",
            role=trace_role,
            backend=self.name,
            model=self.model,
            request_id=request_id,
            queue_key=queue_key,
        )
        formatted_messages = [{"role": "system", "content": system_prompt}] + messages
        payload = {
            "model": self.model,
            "messages": formatted_messages,
            "stream": True,
            "max_tokens": max_tokens,
            "temperature": 0.0,
        }
        if self.chat_template_kwargs:
            payload["chat_template_kwargs"] = dict(self.chat_template_kwargs)
        if response_format is not None:
            payload["response_format"] = response_format

        chunk_count = 0
        first_delta_emitted = False
        wait_started_at = time.perf_counter()
        try:
            async with _semaphore_for(queue_key):
                trace_backend_call(
                    event="queue_acquired",
                    kind="llm",
                    role=trace_role,
                    backend=self.name,
                    model=self.model,
                    request_id=request_id,
                    queue_key=queue_key,
                    wait_ms=_elapsed_ms(wait_started_at),
                )
                async with self._create_client() as client:
                    async with client.stream(
                        "POST",
                        chat_completions_url(self.url),
                        json=payload,
                    ) as response:
                        response.raise_for_status()
                        trace_backend_call(
                            event="response_headers",
                            kind="llm",
                            role=trace_role,
                            backend=self.name,
                            model=self.model,
                            request_id=request_id,
                            queue_key=queue_key,
                            elapsed_ms=_elapsed_ms(started_at),
                        )
                        async for line in response.aiter_lines():
                            try:
                                content = parse_sse_content(line)
                            except json.JSONDecodeError:
                                logger.warning("Invalid LM Studio SSE line ignored: %r", line)
                                continue
                            if content is not None:
                                if not first_delta_emitted:
                                    first_delta_emitted = True
                                    trace_backend_call(
                                        event="first_delta",
                                        kind="llm",
                                        role=trace_role,
                                        backend=self.name,
                                        model=self.model,
                                        request_id=request_id,
                                        queue_key=queue_key,
                                        elapsed_ms=_elapsed_ms(started_at),
                                    )
                                chunk_count += 1
                                yield content
        except Exception as exc:
            trace_backend_call(
                event="error",
                kind="llm",
                role=trace_role,
                backend=self.name,
                model=self.model,
                request_id=request_id,
                queue_key=queue_key,
                total_ms=_elapsed_ms(started_at),
                error=type(exc).__name__,
            )
            raise
        else:
            trace_backend_call(
                event="done",
                kind="llm",
                role=trace_role,
                backend=self.name,
                model=self.model,
                request_id=request_id,
                queue_key=queue_key,
                total_ms=_elapsed_ms(started_at),
                chunk_count=chunk_count,
            )

    def _create_client(self) -> AbstractAsyncContextManager[Any]:
        if self._client_factory is not None:
            return self._client_factory()
        timeout = httpx.Timeout(self.timeout_sec, connect=5.0)
        return httpx.AsyncClient(timeout=timeout)


def _semaphore_for(queue_key: str) -> asyncio.Semaphore:
    if queue_key not in _SEMAPHORES:
        _SEMAPHORES[queue_key] = asyncio.Semaphore(1)
    return _SEMAPHORES[queue_key]


def _elapsed_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000
