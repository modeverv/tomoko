from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any, Literal

from server.session_short_memory import (
    DEFAULT_SHORT_MEMORY_MAX_NOTES,
    DEFAULT_SHORT_MEMORY_TTL_TURNS,
    propose_short_memory_notes,
)
from server.shared.inference.trace import chat_stream_structured_with_trace_role
from server.shared.models import ShortMemoryNote, ShortMemoryProposalResult

logger = logging.getLogger(__name__)

SHORT_MEMORY_EXTRACTION_MAX_TOKENS = 320


async def extract_short_memory_notes(
    *,
    user_text: str,
    reply_text: str,
    current_turn: int,
    default_ttl_turns: int = DEFAULT_SHORT_MEMORY_TTL_TURNS,
    backend: Any | None = None,
) -> ShortMemoryProposalResult:
    if backend is None or not hasattr(backend, "chat_stream_structured"):
        return propose_short_memory_notes(
            user_text=user_text,
            reply_text=reply_text,
            current_turn=current_turn,
            default_ttl_turns=default_ttl_turns,
        )

    try:
        raw_json = "".join(
            [
                chunk
                async for chunk in chat_stream_structured_with_trace_role(
                    backend,
                    _short_memory_extraction_system_prompt(),
                    [
                        {
                            "role": "user",
                            "content": _short_memory_extraction_user_prompt(
                                user_text=user_text,
                                reply_text=reply_text,
                            ),
                        }
                    ],
                    json_schema=_short_memory_extraction_schema(),
                    max_tokens=SHORT_MEMORY_EXTRACTION_MAX_TOKENS,
                    trace_role="memory_extraction",
                )
            ]
        )
        return _parse_llm_short_memory_result(
            raw_json,
            user_text=user_text,
            current_turn=current_turn,
            default_ttl_turns=default_ttl_turns,
        )
    except Exception:
        logger.warning(
            "short memory LLM extraction failed; falling back to heuristic",
            exc_info=True,
        )
        fallback = propose_short_memory_notes(
            user_text=user_text,
            reply_text=reply_text,
            current_turn=current_turn,
            default_ttl_turns=default_ttl_turns,
        )
        return ShortMemoryProposalResult(
            proposals=fallback.proposals,
            decision=fallback.decision,
            reason=fallback.reason,
            raw_text=fallback.raw_text,
            source="heuristic_fallback",
        )


def _short_memory_extraction_system_prompt() -> str:
    return (
        "You extract short working memory notes for Tomoko's next few turns.\n"
        "Decide whether the latest user utterance should be stored in volatile "
        "short memory.\n"
        "Store only temporary working context, short-term intent, or the next "
        "thing to try.\n"
        "Skip greetings, filler, pure acknowledgements, noisy STT fragments, "
        "and questions that do not add a reusable working constraint.\n"
        "Normalize obvious wake words like 智子 or トモコ out of stored notes.\n"
        "Do not create long-term facts. Do not invent details.\n"
        "Return only JSON that matches the schema."
    )


def _short_memory_extraction_user_prompt(*, user_text: str, reply_text: str) -> str:
    return (
        "Latest user transcript:\n"
        f"{user_text}\n\n"
        "Tomoko reply:\n"
        f"{reply_text}\n\n"
        "If storing, rewrite the note as a concise Japanese working note."
    )


def _short_memory_extraction_schema() -> dict[str, Any]:
    return {
        "name": "short_memory_extraction",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["store", "skip"]},
                "reason": {"type": "string"},
                "raw_text": {"type": "string"},
                "proposals": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": [
                                    "working_context",
                                    "short_intent",
                                    "next_trial",
                                ],
                            },
                            "text": {"type": "string"},
                            "confidence": {"type": "number"},
                            "importance": {"type": "number"},
                            "expires_after_turns": {"type": "integer"},
                        },
                        "required": [
                            "kind",
                            "text",
                            "confidence",
                            "importance",
                            "expires_after_turns",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["decision", "reason", "raw_text", "proposals"],
            "additionalProperties": False,
        },
    }


def _parse_llm_short_memory_result(
    raw_json: str,
    *,
    user_text: str,
    current_turn: int,
    default_ttl_turns: int,
) -> ShortMemoryProposalResult:
    payload = json.loads(raw_json)
    decision = payload.get("decision")
    if decision not in {"store", "skip"}:
        raise ValueError(f"invalid short memory decision: {decision!r}")

    proposals: list[ShortMemoryNote] = []
    if decision == "store":
        raw_proposals = payload.get("proposals")
        if not isinstance(raw_proposals, list):
            raise ValueError("short memory proposals must be a list")
        for item in raw_proposals[:DEFAULT_SHORT_MEMORY_MAX_NOTES]:
            if not isinstance(item, dict):
                continue
            text = _normalize_text(str(item.get("text", "")))
            if not text:
                continue
            proposals.append(
                ShortMemoryNote(
                    kind=_coerce_note_kind(item.get("kind")),
                    text=text,
                    confidence=_clamp_float(item.get("confidence"), default=0.6),
                    importance=_clamp_float(item.get("importance"), default=0.6),
                    created_turn=current_turn,
                    expires_after_turns=_coerce_ttl(
                        item.get("expires_after_turns"),
                        default=default_ttl_turns,
                    ),
                    created_at=datetime.now(UTC),
                )
            )

    return ShortMemoryProposalResult(
        proposals=proposals,
        decision=decision,  # type: ignore[arg-type]
        reason=_normalize_text(str(payload.get("reason", ""))) or None,
        raw_text=str(payload.get("raw_text") or user_text),
        source="llm",
    )


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _coerce_note_kind(
    value: object,
) -> Literal["working_context", "short_intent", "next_trial"]:
    if value in {"working_context", "short_intent", "next_trial"}:
        return value  # type: ignore[return-value]
    return "working_context"


def _clamp_float(value: object, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, number))


def _coerce_ttl(value: object, *, default: int) -> int:
    try:
        ttl = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(default, ttl))
