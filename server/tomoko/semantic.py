from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from server.shared.logging import JsonlLogger
from server.shared.models import SemanticSaturationResult

SATURATION_RE = re.compile(r"^SATURATION=([01](?:\.\d+)?)$")
LOWERING_PREFIXES = ("ただ", "でも", "いや", "というか", "一個だけ", "ひとつだけ")
HIGH_CUES = (
    "?",
    "？",
    "教えて",
    "して",
    "ください",
    "お願い",
    "どう",
    "何",
    "なに",
    "予定",
    "トモコ",
    "ともこ",
    "Tomoko",
)


class SaturationLlmBackend(Protocol):
    async def complete(self, prompt: str) -> str: ...


class OpenAICompatibleSaturationBackend:
    def __init__(
        self,
        *,
        url: str,
        model: str,
        max_tokens: int = 16,
        temperature: float = 0.0,
        timeout_sec: float = 15.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout_sec = timeout_sec

    async def complete(self, prompt: str) -> str:
        timeout = httpx.Timeout(self.timeout_sec, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self.url}/v1/chat/completions",
                json=self.payload(prompt),
            )
            response.raise_for_status()
        payload = response.json()
        choices = payload.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return str(message.get("content", "")).strip()

    def payload(self, prompt: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "あなたは会話発話可能判定器です。"
                        "必ず SATURATION=<0.0から1.0の数値> の1行だけを返してください。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "chat_template_kwargs": {"enable_thinking": False},
        }


def create_default_semantic_llm_backend() -> OpenAICompatibleSaturationBackend:
    return OpenAICompatibleSaturationBackend(
        url=os.environ.get("TOMOKO_V2_SEMANTIC_LLM_URL", "http://127.0.0.1:8083"),
        model=os.environ.get(
            "TOMOKO_V2_SEMANTIC_LLM_MODEL",
            "mlx-community/gemma-4-e2b-it-OptiQ-4bit",
        ),
        max_tokens=int(os.environ.get("TOMOKO_V2_SEMANTIC_LLM_MAX_TOKENS", "16")),
        timeout_sec=float(os.environ.get("TOMOKO_V2_SEMANTIC_LLM_TIMEOUT_SEC", "15.0")),
    )


@dataclass(slots=True)
class SemanticSaturationJudge:
    llm_backend: SaturationLlmBackend | None = None
    logger: JsonlLogger | None = None

    async def judge(self, text: str, *, partial: bool = False) -> SemanticSaturationResult:
        if self.llm_backend is None:
            result = deterministic_saturation(
                text,
                source="deterministic_partial" if partial else "deterministic",
            )
            self._log(result)
            return result
        try:
            result = parse_saturation_output(
                await self.llm_backend.complete(saturation_prompt(text)),
                basis_text=text,
                source="llm_partial" if partial else "llm",
            )
        except Exception:
            result = deterministic_saturation(
                text,
                source="deterministic_fallback_partial" if partial else "deterministic_fallback",
            )
        self._log(result)
        return result

    def _log(self, result: SemanticSaturationResult) -> None:
        if self.logger is None:
            return
        self.logger.log(
            "semantic_saturation",
            saturation=result.saturation,
            source=result.source,
            basis_text=result.basis_text,
            result_id=str(result.id),
        )


def saturation_prompt(text: str) -> str:
    return (
        "入力された日本語の途中/最終発話について、"
        "会話相手が今返し始めてよい度合いを 0.0 から 1.0 で判定してください。\n\n"
        "高い値:\n"
        "- 質問、依頼、確認、呼びかけ、返答待ちが明確\n"
        "- 文が完結している、または途中でも相手が短く返せる\n"
        "- 「聞こえますか」「いますか」「どうですか」「お願い」「教えて」など\n\n"
        "低い値:\n"
        "- 「えっと」「あの」「ただ」「でも」「というか」など、まだ続きそう\n"
        "- 文の途中で、相手が返すと割り込みになりそう\n"
        "- 意味が取れない短すぎる断片\n\n"
        "例:\n"
        "TEXT=えっと\n"
        "SATURATION=0.10\n"
        "TEXT=今日の予定を教えて\n"
        "SATURATION=0.95\n"
        "TEXT=ただ、やっぱり\n"
        "SATURATION=0.20\n"
        "TEXT=今の返事ちゃんと聞こえてる\n"
        "SATURATION=0.85\n"
        "TEXT=あのさ、昨日の\n"
        "SATURATION=0.25\n\n"
        f"TEXT={text}"
    )


def parse_saturation_output(
    output: str,
    *,
    basis_text: str = "",
    source: str = "llm",
) -> SemanticSaturationResult:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("saturation output must be exactly one non-empty line")
    match = SATURATION_RE.match(lines[0])
    if match is None:
        raise ValueError("saturation output must be SATURATION=0.0..1.0")
    saturation = float(match.group(1))
    if not 0.0 <= saturation <= 1.0:
        raise ValueError("saturation must be within 0.0..1.0")
    return SemanticSaturationResult(
        saturation=saturation,
        source=source,
        basis_text=basis_text,
    )


def deterministic_saturation(
    text: str,
    *,
    source: str = "deterministic",
) -> SemanticSaturationResult:
    normalized = "".join(text.split())
    if not normalized:
        saturation = 0.0
    elif len(normalized) <= 2:
        saturation = 0.15
    elif normalized.startswith(LOWERING_PREFIXES):
        saturation = 0.35
    elif any(cue in normalized for cue in HIGH_CUES):
        saturation = 0.82
    elif normalized.endswith(("。", "です", "ます", "だよ", "だね")):
        saturation = 0.62
    else:
        saturation = 0.45
    return SemanticSaturationResult(
        saturation=saturation,
        source=source,
        basis_text=text,
    )


def stable_prefix(texts: list[str] | tuple[str, ...]) -> str:
    if not texts:
        return ""
    prefix = texts[0]
    for text in texts[1:]:
        while prefix and not text.startswith(prefix):
            prefix = prefix[:-1]
        if not prefix:
            return ""
    return prefix
