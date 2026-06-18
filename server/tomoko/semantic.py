from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

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
        "次の日本語発話が、Tomokoが返答を始めてよい程度に意味的に飽和しているかを"
        "0.0から1.0で返す。出力は必ず SATURATION=<number> の1行だけ。\n"
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
