from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Callable, Iterable
from typing import Any
from uuid import uuid4

from server.shared.inference.backends.base import InferenceBackend
from server.shared.inference.trace import trace_backend_call

logger = logging.getLogger(__name__)

ModelLoader = Callable[[str, str | None], tuple[Any, Any]]
StreamGenerator = Callable[..., Iterable[Any]]

# Phase TT-v2.10c: KVキャッシュ（prompt cache）の同時保持数。
# ターン単位で使うため実質1つ、切替の取りこぼし用に1つの余裕を持つ。
MAX_PROMPT_CACHES = 2


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
        prompt_cache_factory: Callable[[Any], Any] | None = None,
        prompt_processor: Callable[[Any, Any, list[int]], None] | None = None,
        cache_trimmer: Callable[[Any, int], bool] | None = None,
    ) -> None:
        self.name = name
        self.model_name = model
        self.adapter_path = adapter_path
        self.privacy_allowed = privacy_allowed
        self.max_tokens = max_tokens
        self._model_loader = model_loader or _load_model
        self._stream_generator = stream_generator or _stream_generate
        self._prompt_cache_factory = prompt_cache_factory or _make_prompt_cache
        self._prompt_processor = prompt_processor or _process_prompt_tokens
        self._cache_trimmer = cache_trimmer or _trim_prompt_cache
        self._model: Any | None = None
        self._tokenizer: Any | None = None
        # Phase TT-v2.10c: cache_key -> (KVキャッシュ, キャッシュ済みトークン列)。
        # 規約: ターン内 append-only、ターン終了で drop_prefill により破棄。
        self._prompt_caches: dict[str, tuple[Any, list[int]]] = {}

    async def warm_up(self) -> None:
        async for _chunk in self.chat_stream(
            "あなたは日本語で短く答えるアシスタントです。",
            [{"role": "user", "content": "短く返事して。"}],
        ):
            pass

    async def prefill(
        self,
        cache_key: str,
        system_prompt: str,
        messages: list[dict[str, str]],
    ) -> dict[str, float | int]:
        """プロンプトの KV キャッシュを構築/延長する（生成はしない）。

        同じ cache_key で再呼び出しすると、前回プロンプトとの共通プレフィックスは
        再利用し、差分トークンのみを処理する。戻り値は計測情報
        （new_tokens / total_tokens / elapsed_ms）。
        """
        return await asyncio.to_thread(
            self._prefill_sync, cache_key, system_prompt, messages
        )

    def _prefill_sync(
        self,
        cache_key: str,
        system_prompt: str,
        messages: list[dict[str, str]],
    ) -> dict[str, float | int]:
        started_at = time.perf_counter()
        model, tokenizer = self._load()
        prompt = _build_chat_prompt(tokenizer, system_prompt, messages)
        tokens = list(tokenizer.encode(prompt))

        entry = self._prompt_caches.get(cache_key)
        if entry is None:
            cache = self._prompt_cache_factory(model)
            cached_tokens: list[int] = []
        else:
            cache, cached_tokens = entry
            common = _common_prefix_len(cached_tokens, tokens)
            if common < len(cached_tokens):
                # 前回より短い/分岐したプロンプト → 共通部分まで巻き戻す
                if self._cache_trimmer(cache, len(cached_tokens) - common):
                    cached_tokens = cached_tokens[:common]
                else:
                    cache = self._prompt_cache_factory(model)
                    cached_tokens = []

        new_tokens = tokens[len(cached_tokens):]
        if new_tokens:
            self._prompt_processor(model, cache, new_tokens)

        self._prompt_caches[cache_key] = (cache, tokens)
        self._evict_prompt_caches(keep=cache_key)

        elapsed_ms = _elapsed_ms(started_at)
        logger.info(
            "MLXLMBackend prefill backend=%s cache_key=%s new_tokens=%d "
            "total_tokens=%d elapsed_ms=%.1f",
            self.name,
            cache_key,
            len(new_tokens),
            len(tokens),
            elapsed_ms,
        )
        return {
            "new_tokens": len(new_tokens),
            "total_tokens": len(tokens),
            "elapsed_ms": elapsed_ms,
        }

    async def drop_prefill(self, cache_key: str) -> None:
        self._prompt_caches.pop(cache_key, None)

    def _evict_prompt_caches(self, *, keep: str) -> None:
        while len(self._prompt_caches) > MAX_PROMPT_CACHES:
            for key in self._prompt_caches:
                if key != keep:
                    del self._prompt_caches[key]
                    break
            else:
                break

    async def chat_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        *,
        trace_role: str | None = None,
        cache_key: str | None = None,
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

            # Phase TT-v2.10c: prefill 済み KV キャッシュがあれば未処理サフィックスのみ
            # デコータに渡す。デコードでキャッシュは汚れるため使い切り（pop）。
            prompt_input: str | list[int] = prompt
            prompt_cache = None
            if cache_key is not None:
                entry = self._prompt_caches.pop(cache_key, None)
                if entry is not None:
                    cache, cached_tokens = entry
                    tokens = list(tokenizer.encode(prompt))
                    common = _common_prefix_len(cached_tokens, tokens)
                    usable = common == len(cached_tokens) or self._cache_trimmer(
                        cache, len(cached_tokens) - common
                    )
                    if usable:
                        cached_len = min(common, len(cached_tokens))
                        if cached_len >= len(tokens):
                            # 全トークンがキャッシュ済み → 最後の1トークンを巻き戻して入力にする
                            if self._cache_trimmer(cache, cached_len - len(tokens) + 1):
                                cached_len = len(tokens) - 1
                            else:
                                cached_len = -1  # trim 不可 → キャッシュ不使用
                        if cached_len >= 0:
                            prompt_input = tokens[cached_len:]
                            prompt_cache = cache
                            logger.info(
                                "MLXLMBackend using prompt cache backend=%s cache_key=%s "
                                "cached_tokens=%d suffix_tokens=%d",
                                self.name,
                                cache_key,
                                cached_len,
                                len(tokens) - cached_len,
                            )

            stream_kwargs: dict[str, Any] = {}
            if prompt_cache is not None:
                stream_kwargs["prompt_cache"] = prompt_cache
            for response in self._stream_generator(
                model,
                tokenizer,
                prompt_input,
                self.max_tokens,
                **stream_kwargs,
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
    prompt: str | list[int],
    max_tokens: int,
    prompt_cache: Any | None = None,
):
    from mlx_lm import stream_generate
    from mlx_lm.sample_utils import make_sampler

    kwargs: dict[str, Any] = {}
    if prompt_cache is not None:
        kwargs["prompt_cache"] = prompt_cache
    yield from stream_generate(
        model,
        tokenizer,
        prompt,
        max_tokens=max_tokens,
        sampler=make_sampler(temp=0.0),
        **kwargs,
    )


def _make_prompt_cache(model: Any) -> Any:
    from mlx_lm.models.cache import make_prompt_cache

    return make_prompt_cache(model)


def _trim_prompt_cache(cache: Any, num_tokens: int) -> bool:
    """キャッシュ末尾から num_tokens 分を巻き戻す。trim 不可なら False。"""
    if num_tokens <= 0:
        return True
    from mlx_lm.models.cache import can_trim_prompt_cache, trim_prompt_cache

    if not can_trim_prompt_cache(cache):
        return False
    trim_prompt_cache(cache, num_tokens)
    return True


def _process_prompt_tokens(
    model: Any,
    cache: Any,
    tokens: list[int],
    chunk_size: int = 512,
) -> None:
    """トークン列をモデルに流して KV キャッシュを伸ばす（logits は捨てる）。"""
    import mlx.core as mx

    for start in range(0, len(tokens), chunk_size):
        chunk = mx.array(tokens[start:start + chunk_size])[None]
        model(chunk, cache=cache)
        mx.eval([c.state for c in cache])


def _common_prefix_len(a: list[int], b: list[int]) -> int:
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n


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
