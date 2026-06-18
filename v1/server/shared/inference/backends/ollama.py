from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from uuid import uuid4

from ollama import AsyncClient

from server.shared.inference.backends.base import InferenceBackend
from server.shared.inference.trace import trace_backend_call


class OllamaBackend(InferenceBackend):
    def __init__(self, name: str, url: str, model: str, privacy_allowed: bool = True):
        self.name = name
        self.url = url
        self.model = model
        self.privacy_allowed = privacy_allowed
        self.client = AsyncClient(host=url)

    async def chat_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        *,
        trace_role: str | None = None,
    ) -> AsyncGenerator[str, None]:
        request_id = str(uuid4())
        role = trace_role or "unknown"
        started_at = time.perf_counter()
        trace_backend_call(
            event="start",
            kind="llm",
            role=role,
            backend=self.name,
            model=self.model,
            request_id=request_id,
            queue_key=f"ollama:{self.url}",
        )
        chunk_count = 0
        first_delta_emitted = False
        formatted_messages = [{"role": "system", "content": system_prompt}] + messages

        try:
            response = await self.client.chat(
                model=self.model,
                messages=formatted_messages,  # type: ignore[arg-type]
                stream=True,
            )
            async for part in response:  # type: ignore[union-attr]
                content = None
                if hasattr(part, "message") and part.message and hasattr(part.message, "content"):
                    content = part.message.content
                elif isinstance(part, dict):
                    if "message" in part and "content" in part["message"]:
                        content = part["message"]["content"]
                if content:
                    if not first_delta_emitted:
                        first_delta_emitted = True
                        trace_backend_call(
                            event="first_delta",
                            kind="llm",
                            role=role,
                            backend=self.name,
                            model=self.model,
                            request_id=request_id,
                            queue_key=f"ollama:{self.url}",
                            elapsed_ms=_elapsed_ms(started_at),
                        )
                    chunk_count += 1
                    yield content
        except Exception as exc:
            trace_backend_call(
                event="error",
                kind="llm",
                role=role,
                backend=self.name,
                model=self.model,
                request_id=request_id,
                queue_key=f"ollama:{self.url}",
                total_ms=_elapsed_ms(started_at),
                error=type(exc).__name__,
            )
            raise
        else:
            trace_backend_call(
                event="done",
                kind="llm",
                role=role,
                backend=self.name,
                model=self.model,
                request_id=request_id,
                queue_key=f"ollama:{self.url}",
                total_ms=_elapsed_ms(started_at),
                chunk_count=chunk_count,
            )


def _elapsed_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000
