from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable, Iterable
from typing import Any

DEFAULT_MODEL = "mlx-community/gemma-4-e2b-it-4bit"
DEFAULT_MAX_TOKENS = 80

SYSTEM_PROMPT = """あなたは音声読み上げ用の日本語正規化器です。
入力文を、TTS が自然に読める日本語へ変換してください。

規則:
- 出力は変換後の本文だけ。説明、引用符、箇条書き、前置きは禁止。
- 意味は変えない。
- 入力の文の数と順序を保つ。複数文を一文に結合しない。
- 入力に句点、読点、疑問符、感嘆符があれば、対応する位置に自然な日本語の句読点を残す。
- 文末には必ず日本語の句点、疑問符、感嘆符のいずれかを付ける。
- 英語、略語、時刻、日付、数字、単位は自然な日本語表記へ直す。
- 英字は原則として残さない。一般語はカタカナより自然な日本語訳を優先する。
- すでに自然な日本語ならほぼそのまま返す。
- 絵文字、装飾記号、Markdown は出さない。
"""

ModelLoader = Callable[[str], tuple[Any, Any]]
StreamGenerator = Callable[[Any, Any, str, int], Iterable[Any]]

_ASCII_ALPHA_RE = re.compile(r"[A-Za-z]")
_TIME_RE = re.compile(
    r"(?:\b\d{1,2}\s*(?:am|pm|AM|PM)\b|\b\d{1,2}[:：]\d{2}\b)"
)
_NON_JAPANESE_SCRIPT_RE = re.compile(
    "["
    "\u0370-\u03ff"  # Greek
    "\u0400-\u052f"  # Cyrillic
    "\u0590-\u05ff"  # Hebrew
    "\u0600-\u06ff"  # Arabic
    "\u0900-\u097f"  # Devanagari
    "\uac00-\ud7af"  # Hangul
    "]"
)

logger = logging.getLogger(__name__)


class ReplySpeechNormalizer:
    """Normalizes only risky TTS text with a local MLX Gemma model."""

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        model_loader: ModelLoader | None = None,
        stream_generator: StreamGenerator | None = None,
    ) -> None:
        self.model_name = model_name
        self.max_tokens = max_tokens
        self._model_loader = model_loader or _load_model
        self._stream_generator = stream_generator or _stream_generate
        self._model: Any | None = None
        self._tokenizer: Any | None = None

    async def warm_up(self) -> None:
        await self.normalize("today は 3pm です。")

    async def normalize(self, text: str) -> str:
        stripped = text.strip()
        if not stripped:
            return stripped
        if not self.should_normalize(stripped):
            return stripped

        started_at = time.perf_counter()
        normalized = await asyncio.to_thread(self._normalize_sync, stripped)
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        if not normalized:
            logger.info(
                "ReplySpeechNormalizer fallback original elapsed_ms=%.1f text=%r",
                elapsed_ms,
                stripped,
            )
            return stripped
        logger.info(
            "ReplySpeechNormalizer normalized elapsed_ms=%.1f input=%r output=%r",
            elapsed_ms,
            stripped,
            normalized,
        )
        return normalized

    def should_normalize(self, text: str) -> bool:
        return bool(
            _ASCII_ALPHA_RE.search(text)
            or _TIME_RE.search(text)
            or _NON_JAPANESE_SCRIPT_RE.search(text)
        )

    def _normalize_sync(self, text: str) -> str:
        model, tokenizer = self._load()
        prompt = _build_prompt(tokenizer, text)
        output_parts: list[str] = []
        for response in self._stream_generator(
            model,
            tokenizer,
            prompt,
            self.max_tokens,
        ):
            output_parts.append(getattr(response, "text", ""))
        output = _polish_common_tts_terms(_clean_model_output("".join(output_parts)))
        return _restore_terminal_punctuation(output, source=text)

    def _load(self) -> tuple[Any, Any]:
        if self._model is None or self._tokenizer is None:
            started_at = time.perf_counter()
            self._model, self._tokenizer = self._model_loader(self.model_name)
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            logger.info(
                "ReplySpeechNormalizer model loaded model=%s elapsed_ms=%.1f",
                self.model_name,
                elapsed_ms,
            )
        return self._model, self._tokenizer


def _load_model(model_name: str) -> tuple[Any, Any]:
    from mlx_vlm import load

    return load(model_name)


def _stream_generate(
    model: Any,
    tokenizer: Any,
    prompt: str,
    max_tokens: int,
):
    from mlx_vlm import stream_generate

    yield from stream_generate(
        model,
        tokenizer,
        prompt,
        max_tokens=max_tokens,
        temperature=0.0,
    )


def _build_prompt(tokenizer: Any, text: str) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]
    if hasattr(tokenizer, "apply_chat_template"):
        return tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
    return f"{SYSTEM_PROMPT}\n\n入力:\n{text}\n\n出力:\n"


def _clean_model_output(output: str) -> str:
    cleaned = output.strip()
    cleaned = re.sub(r"^```(?:text|日本語)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    cleaned = re.sub(r"^(出力|変換後|読み上げ用)[:：]\s*", "", cleaned).strip()
    if "\n" in cleaned:
        cleaned = cleaned.splitlines()[0].strip()
    return cleaned.strip("「」\"'")


def _polish_common_tts_terms(output: str) -> str:
    polished = output
    polished = polished.replace("クイックに", "すぐに")
    polished = polished.replace("クイックで", "すぐに")
    return polished


def _restore_terminal_punctuation(output: str, *, source: str) -> str:
    stripped = output.strip()
    if not stripped:
        return stripped
    if stripped[-1] in "。！？!?":
        return stripped
    source_tail = source.strip()
    if source_tail.endswith(("？", "?")):
        return f"{stripped}？"
    if source_tail.endswith(("！", "!")):
        return f"{stripped}！"
    if source_tail.endswith("。"):
        return f"{stripped}。"
    return stripped
