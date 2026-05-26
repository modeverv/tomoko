from __future__ import annotations

import json
import logging
from typing import Any

from server.shared.candidate import (
    CandidateSeed,
    EvaluatedUtterance,
    ThinkerEvaluationContext,
)
from server.shared.inference.router import InferenceRouter
from server.shared.inference.trace import chat_stream_with_trace_role
from server.thinker.evaluator.base import UtteranceEvaluator

logger = logging.getLogger(__name__)

EVALUATOR_OUTPUT_SCHEMA = {
    "should_keep": "bool",
    "generated_text": "str | null",
    "priority": "float 0.0..1.0",
    "urgent": "bool",
    "reason": "str",
}

_SYSTEM_PROMPT = """\
あなたはTomokoの自発発話候補を評価する background evaluator です。
会話原文や全ログを要求せず、渡された要約・用語・人格 slice だけで判断してください。
generated_text は「興味メモ」ではなく、Tomoko が実際に話しかける会話開始用の短文にしてください。
制約:
- 1〜2文にする
- 何の話か一発でわかるようにする
- ユーザーに説明責任を押しつけない
- 事実断定より「気になっている」「あとで話したい」に寄せる
- 質問で終える場合は1つだけにする
- seed_source が world_observation など直前文脈と別件になりやすい話題なら、
  「さっきの話とは別で、」などの橋渡しを入れる
- 「を動かすための専用チップ」のような主語欠け断片を返さない
返答は JSON object だけにしてください。schema:
{
  "should_keep": true | false,
  "generated_text": "実際に話す短い日本語" | null,
  "priority": 0.0-1.0,
  "urgent": true | false,
  "reason": "短い判断理由"
}
"""


class LLMUtteranceEvaluator(UtteranceEvaluator):
    def __init__(self, router: InferenceRouter) -> None:
        self.router = router

    async def evaluate(
        self,
        seed: CandidateSeed,
        context: ThinkerEvaluationContext,
    ) -> EvaluatedUtterance | None:
        try:
            backend = await self.router.select("candidate_gen", "privacy")
            chunks = [
                chunk
                async for chunk in chat_stream_with_trace_role(
                    backend,
                    _SYSTEM_PROMPT,
                    [_user_message(seed, context)],
                    trace_role="candidate_gen",
                )
            ]
            return _parse_evaluation("".join(chunks), seed)
        except Exception as exc:
            logger.info(
                "LLM utterance evaluator discarded seed source=%s reason=%s",
                seed.source,
                type(exc).__name__,
            )
            return None


def _user_message(
    seed: CandidateSeed,
    context: ThinkerEvaluationContext,
) -> dict[str, str]:
    sections = [
        f"observed_at: {context.observed_at.isoformat()}",
        f"device_id: {context.device_id or 'unknown'}",
        f"attention_mode: {context.attention_mode or 'unknown'}",
        f"seed_text: {seed.seed_text}",
        f"seed_source: {seed.source}",
        f"seed_priority: {seed.priority}",
        f"seed_urgent: {seed.urgent}",
        f"recent_summary: {context.recent_summary or ''}",
        _format_items("session_summaries", context.session_summaries),
        _format_items("lexicon_terms", context.lexicon_terms),
        _format_items("persona_notes", context.persona_notes),
    ]
    return {"role": "user", "content": "\n".join(sections)}


def _format_items(label: str, items: tuple[str, ...]) -> str:
    if not items:
        return f"{label}: []"
    return f"{label}:\n" + "\n".join(f"- {item}" for item in items)


def _parse_evaluation(raw_text: str, seed: CandidateSeed) -> EvaluatedUtterance | None:
    payload = _load_json_object(raw_text)
    should_keep = bool(payload.get("should_keep", False))
    generated_text = _optional_text(payload.get("generated_text"))
    priority = _clamp_priority(payload.get("priority", seed.priority))
    urgent = bool(payload.get("urgent", seed.urgent))
    reason = str(payload.get("reason") or "no reason")

    if not should_keep:
        return EvaluatedUtterance(
            should_keep=False,
            generated_text=None,
            priority=priority,
            urgent=urgent,
            reason=reason,
            context_tags=(*seed.context_tags, "evaluated_by:llm"),
        )
    if generated_text is None:
        return None
    generated_text = _normalize_generated_text(generated_text, seed)
    if generated_text is None:
        return None

    return EvaluatedUtterance(
        should_keep=True,
        generated_text=generated_text,
        priority=priority,
        urgent=urgent,
        reason=reason,
        context_tags=(*seed.context_tags, "evaluated_by:llm"),
    )


def _load_json_object(raw_text: str) -> dict[str, Any]:
    stripped = raw_text.strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(stripped[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("evaluator response must be a JSON object")
    return payload


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_generated_text(text: str, seed: CandidateSeed) -> str | None:
    normalized = " ".join(line.strip() for line in text.splitlines() if line.strip())
    normalized = normalized.strip()
    if not normalized:
        return None
    if _looks_fragmentary(normalized):
        return None
    if _asserts_latest_knowledge(normalized):
        return None
    if len(normalized) > 90:
        return None
    if _needs_topic_shift_bridge(seed) and not _has_topic_shift_bridge(normalized):
        normalized = f"さっきの話とは別で、{normalized}"
    return normalized


def _looks_fragmentary(text: str) -> bool:
    fragment_starts = (
        "を",
        "が",
        "に",
        "で",
        "へ",
        "と",
        "のため",
        "ための",
    )
    return text.startswith(fragment_starts)


def _asserts_latest_knowledge(text: str) -> bool:
    forbidden = (
        "最新情報を知っている",
        "最新情報によると",
        "確実に",
        "間違いなく",
    )
    return any(phrase in text for phrase in forbidden)


def _needs_topic_shift_bridge(seed: CandidateSeed) -> bool:
    return seed.source.startswith("world_observation") or (
        "topic_shift_bridge_required" in seed.context_tags
    )


def _has_topic_shift_bridge(text: str) -> bool:
    bridges = (
        "別件",
        "さっきの話とは別",
        "今じゃなければ後で",
        "あとでいいんだけど",
        "話は変わるんだけど",
    )
    return any(bridge in text for bridge in bridges)


def _clamp_priority(value: object) -> float:
    try:
        priority = float(value)
    except (TypeError, ValueError):
        priority = 0.5
    return min(1.0, max(0.0, priority))
