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

SHORT_MEMORY_EXTRACTION_MAX_TOKENS = 160
SHORT_MEMORY_LLM_CONFIDENCE = 0.85


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
    if _should_skip_llm_extraction(user_text):
        return ShortMemoryProposalResult(
            proposals=[],
            decision="skip",
            reason="deterministic short memory guard",
            raw_text=user_text,
            source="llm",
        )

    try:
        system_prompt = _short_memory_extraction_system_prompt()
        messages = [
            {
                "role": "user",
                "content": _short_memory_extraction_user_prompt(
                    user_text=user_text,
                ),
            }
        ]
        logger.info(
            "short memory extraction llm_prompt backend=%s payload=%s",
            getattr(backend, "name", "unknown"),
            json.dumps(
                {
                    "system_prompt": system_prompt,
                    "messages": messages,
                    "max_tokens": SHORT_MEMORY_EXTRACTION_MAX_TOKENS,
                },
                ensure_ascii=False,
            ),
        )
        raw_json = "".join(
            [
                chunk
                async for chunk in chat_stream_structured_with_trace_role(
                    backend,
                    system_prompt,
                    messages,
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
            "short memory LLM extraction failed; skipping fallback store",
            exc_info=True,
        )
        return ShortMemoryProposalResult(
            proposals=[],
            decision="skip",
            reason="llm extraction failed",
            raw_text=user_text,
            source="heuristic_fallback",
        )


def _short_memory_extraction_system_prompt() -> str:
    return (
        "You extract only the exact things Tomoko should remember for the next "
        "few turns.\n"
        "Return remember_items as an array. Return an empty array when there is "
        "nothing reusable to remember.\n"
        "Each item must have only text and mode.\n"
        "For explicit requests like 'ABCを覚えて' or '123を覚えて', output only "
        "the target text such as 'ABC' or '123' with mode='verbatim'.\n"
        "For temporary project constraints, current intent, or what to try next, "
        "output a concise Japanese note with mode='working_context'.\n"
        "For spoken task tracking, store task lists, completed tasks, and added "
        "tasks as working_context notes.\n"
        "Do not summarize, count, reinterpret, translate, or invent details.\n"
        "Do not extract answers from Tomoko replies; only the latest user "
        "transcript is provided.\n"
        "Skip greetings, filler, pure acknowledgements, noisy STT fragments, "
        "recall questions like '覚えてる？' or '教えて', and questions that do "
        "not add reusable working context.\n"
        "Return only JSON that matches the schema."
    )


def _short_memory_extraction_user_prompt(*, user_text: str) -> str:
    return (
        "Latest user transcript:\n"
        f"{user_text}\n\n"
        "Extract only items that should be remembered. Keep verbatim targets "
        "exactly as spoken, except remove obvious wake words like 智子 or トモコ."
    )


def _short_memory_extraction_schema() -> dict[str, Any]:
    return {
        "name": "short_memory_extraction",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "remember_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "mode": {
                                "type": "string",
                                "enum": [
                                    "verbatim",
                                    "working_context",
                                ],
                            },
                        },
                        "required": [
                            "text",
                            "mode",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["remember_items"],
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
    raw_items = payload.get("remember_items")
    if not isinstance(raw_items, list):
        raise ValueError("short memory remember_items must be a list")
    proposals: list[ShortMemoryNote] = []
    seen: set[tuple[str, str]] = set()
    for item in raw_items[:DEFAULT_SHORT_MEMORY_MAX_NOTES]:
        if not isinstance(item, dict):
            continue
        text = _normalize_text(str(item.get("text", "")))
        if not text:
            continue
        mode = _coerce_note_mode(item.get("mode"))
        key = (mode, text.casefold())
        if key in seen:
            continue
        seen.add(key)
        proposals.append(
            ShortMemoryNote(
                kind=mode,
                text=text,
                confidence=SHORT_MEMORY_LLM_CONFIDENCE,
                importance=SHORT_MEMORY_LLM_CONFIDENCE,
                created_turn=current_turn,
                expires_after_turns=default_ttl_turns,
                created_at=datetime.now(UTC),
            )
        )
    decision = "store" if proposals else "skip"

    return ShortMemoryProposalResult(
        proposals=proposals,
        decision=decision,
        reason="llm returned remember_items" if proposals else "llm returned no items",
        raw_text=user_text,
        source="llm",
    )


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _coerce_note_mode(
    value: object,
) -> Literal["working_context", "verbatim"]:
    if value in {"working_context", "verbatim"}:
        return value  # type: ignore[return-value]
    return "working_context"


def _should_skip_llm_extraction(user_text: str) -> bool:
    text = _normalize_text(user_text)
    if len(text) < 8:
        return True
    hearing_checks = (
        "聞こえますか",
        "聞こえてますか",
        "聞こえる",
    )
    if any(phrase in text for phrase in hearing_checks):
        return True
    recall_markers = (
        "覚えてる",
        "覚えている",
        "思い出せる",
        "思い出して",
    )
    if any(marker in text for marker in recall_markers):
        return True
    recall_requests = (
        "答えて",
        "教えて",
        "何だっけ",
        "なんだっけ",
        "何を優先",
    )
    if any(marker in text for marker in recall_requests):
        return True
    if "教えて" in text and ("さっき" in text or "前" in text or "覚え" in text):
        return True
    return False
