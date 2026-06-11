from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator, Callable, Iterable
from typing import Any
from uuid import uuid4

from server.shared.inference.backends.base import InferenceBackend
from server.shared.inference.trace import trace_backend_call

logger = logging.getLogger(__name__)

ModelLoader = Callable[[str, str | None], tuple[Any, Any]]
StreamGenerator = Callable[[Any, Any, str, int], Iterable[Any]]


class MLXLMBackend(InferenceBackend):
    def __init__(
        self,
        *,
        name: str,
        model: str,
        adapter_path: str | None = None,
        privacy_allowed: bool = True,
        max_tokens: int = 180,
        model_loader: ModelLoader | None = None,
        stream_generator: StreamGenerator | None = None,
    ) -> None:
        self.name = name
        self.model_name = model
        self.adapter_path = adapter_path
        self.privacy_allowed = privacy_allowed
        self.max_tokens = max_tokens
        self._model_loader = model_loader or _load_model
        self._stream_generator = stream_generator or _stream_generate
        self._model: Any | None = None
        self._tokenizer: Any | None = None

    async def warm_up(self) -> None:
        async for _chunk in self.chat_stream(
            "あなたは日本語で短く答えるアシスタントです。",
            [{"role": "user", "content": "短く返事して。"}],
        ):
            pass

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
            model=self.model_name,
            request_id=request_id,
            queue_key="local_mlx",
        )
        chunk_count = 0
        first_delta_emitted = False
        try:
            model, tokenizer = self._load()
            prompt = _build_chat_prompt(tokenizer, system_prompt, messages)
            for response in self._stream_generator(
                model,
                tokenizer,
                prompt,
                self.max_tokens,
            ):
                text = getattr(response, "text", "")
                if text:
                    if not first_delta_emitted:
                        first_delta_emitted = True
                        trace_backend_call(
                            event="first_delta",
                            kind="llm",
                            role=role,
                            backend=self.name,
                            model=self.model_name,
                            request_id=request_id,
                            queue_key="local_mlx",
                            elapsed_ms=_elapsed_ms(started_at),
                        )
                    chunk_count += 1
                    yield text
        except Exception as exc:
            trace_backend_call(
                event="error",
                kind="llm",
                role=role,
                backend=self.name,
                model=self.model_name,
                request_id=request_id,
                queue_key="local_mlx",
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
                model=self.model_name,
                request_id=request_id,
                queue_key="local_mlx",
                total_ms=_elapsed_ms(started_at),
                chunk_count=chunk_count,
            )

    async def chat_stream_structured(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        *,
        json_schema: dict[str, Any],
        max_tokens: int | None = None,
        trace_role: str | None = None,
    ) -> AsyncGenerator[str, None]:
        import json
        schema_desc = json.dumps(json_schema, ensure_ascii=False)
        enhanced_system_prompt = (
            f"{system_prompt}\n\n"
            f"重要: あなたの出力は以下の JSON Schema に完全に準拠した JSON オブジェクトのみである必要があります。\n"
            f"他のいかなるテキスト（解説、コードブロックのマーク等）も含めてはいけません。\n"
            f"Schema:\n{schema_desc}"
        )
        async for chunk in self.chat_stream(
            enhanced_system_prompt,
            messages,
            trace_role=trace_role,
        ):
            yield chunk

    def _load(self) -> tuple[Any, Any]:
        if self._model is None or self._tokenizer is None:
            started_at = time.perf_counter()
            self._model, self._tokenizer = self._model_loader(
                self.model_name, self.adapter_path
            )
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            logger.info(
                "MLXLMBackend model loaded backend=%s model=%s elapsed_ms=%.1f",
                self.name,
                self.model_name,
                elapsed_ms,
            )
        return self._model, self._tokenizer


def _load_model(model_name: str, adapter_path: str | None = None) -> tuple[Any, Any]:
    from mlx_lm import load

    if adapter_path:
        return load(model_name, adapter_path=adapter_path)
    return load(model_name)


def _stream_generate(
    model: Any,
    tokenizer: Any,
    prompt: str,
    max_tokens: int,
):
    from mlx_lm import stream_generate
    from mlx_lm.sample_utils import make_sampler

    yield from stream_generate(
        model,
        tokenizer,
        prompt,
        max_tokens=max_tokens,
        sampler=make_sampler(temp=0.0),
    )


def _build_chat_prompt(
    tokenizer: Any,
    system_prompt: str,
    messages: list[dict[str, str]],
) -> str:
    chat_messages = [{"role": "system", "content": system_prompt}] + messages
    if hasattr(tokenizer, "apply_chat_template"):
        return tokenizer.apply_chat_template(
            chat_messages,
            tokenize=False,
            add_generation_prompt=True,
        )
    rendered = "\n".join(
        f"{message['role']}:\n{message['content']}" for message in chat_messages
    )
    return f"{rendered}\nassistant:\n"


def _elapsed_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000
