from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

from server.shared.models import PromptRequest, PromptScope


class ChatBackend:
    async def stream(self, request: PromptRequest) -> AsyncIterator[str]:
        raise NotImplementedError


class StaticChatBackend(ChatBackend):
    def __init__(self, deltas: list[str]) -> None:
        self._deltas = deltas

    async def stream(self, request: PromptRequest) -> AsyncIterator[str]:
        for delta in self._deltas:
            yield delta


class OpenAICompatibleChatBackend(ChatBackend):
    def __init__(
        self,
        *,
        url: str,
        model: str,
        max_tokens: int = 180,
        temperature: float = 0.0,
        chat_template_kwargs: dict[str, Any] | None = None,
        timeout_sec: float = 60.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.chat_template_kwargs = dict(chat_template_kwargs or {})
        self.timeout_sec = timeout_sec

    async def stream(self, request: PromptRequest) -> AsyncIterator[str]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": _messages_for_request(request),
            "stream": True,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if self.chat_template_kwargs:
            payload["chat_template_kwargs"] = dict(self.chat_template_kwargs)
        timeout = httpx.Timeout(self.timeout_sec, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{self.url}/v1/chat/completions",
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    content = parse_openai_sse_content(line)
                    if content:
                        yield content


def create_default_real_chat_backend() -> OpenAICompatibleChatBackend:
    return OpenAICompatibleChatBackend(
        url=os.environ.get("TOMOKO_V2_LLM_URL", "http://127.0.0.1:8082"),
        model=os.environ.get("TOMOKO_V2_LLM_MODEL", "gemma-4-26b-a4b-it-mlx"),
        max_tokens=int(os.environ.get("TOMOKO_V2_LLM_MAX_TOKENS", "180")),
        chat_template_kwargs={"enable_thinking": False},
    )


def _messages_for_request(request: PromptRequest) -> list[dict[str, str]]:
    if request.scope == PromptScope.SHORT:
        system = "EMOTION:<label> の1行と、短い日本語1文だけを返す。"
    else:
        system = "あなたはTTSで自然に読める日本語だけで返す。"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": request.prompt_text},
    ]


def parse_openai_sse_content(line: str) -> str | None:
    if not line.startswith("data:"):
        return None
    data = line.removeprefix("data:").strip()
    if not data or data == "[DONE]":
        return None
    payload = json.loads(data)
    choices = payload.get("choices")
    if not choices:
        return None
    delta = choices[0].get("delta", {})
    return delta.get("content")
