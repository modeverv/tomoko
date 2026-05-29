from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from server.shared.models import ShortMemoryNote, ShortMemoryProposalResult

DEFAULT_SHORT_MEMORY_MAX_NOTES = 5
DEFAULT_SHORT_MEMORY_TTL_TURNS = 4

_WORKING_CONTEXT_CUES = (
    "したい",
    "してほしい",
    "試したい",
    "確認したい",
    "見たい",
    "優先",
    "まだ",
    "まず",
    "次",
    "あとで",
    "hot path",
    "DB",
    "UI",
    "memory",
    "メモ",
    "永続化",
)


class ShortMemoryBuffer:
    def __init__(
        self,
        *,
        max_notes: int = DEFAULT_SHORT_MEMORY_MAX_NOTES,
        default_ttl_turns: int = DEFAULT_SHORT_MEMORY_TTL_TURNS,
    ) -> None:
        self.max_notes = max_notes
        self.default_ttl_turns = default_ttl_turns
        self._notes: list[ShortMemoryNote] = []

    def append(self, note: ShortMemoryNote) -> ShortMemoryNote:
        stored = note
        if stored.note_id is None:
            stored = _replace_note_id(stored, str(uuid4()))
        self._notes.append(stored)
        if len(self._notes) > self.max_notes:
            self._notes = self._notes[-self.max_notes :]
        return stored

    def expire_by_turn(self, *, current_turn: int) -> list[ShortMemoryNote]:
        active: list[ShortMemoryNote] = []
        expired: list[ShortMemoryNote] = []
        for note in self._notes:
            if _remaining_turns(note, current_turn=current_turn) <= 0:
                expired.append(note)
            else:
                active.append(note)
        self._notes = active
        return expired

    def read_for_prompt(self, *, current_turn: int) -> list[ShortMemoryNote]:
        return [
            note
            for note in self._notes
            if _remaining_turns(note, current_turn=current_turn) > 0
        ]

    def snapshot_for_ui(self, *, current_turn: int) -> dict[str, object]:
        return {
            "current_turn": current_turn,
            "notes": [
                {
                    "id": note.note_id,
                    "kind": note.kind,
                    "text": note.text,
                    "confidence": note.confidence,
                    "importance": note.importance,
                    "created_turn": note.created_turn,
                    "expires_after_turns": note.expires_after_turns,
                    "remaining_turns": _remaining_turns(
                        note, current_turn=current_turn
                    ),
                    "status": "accepted",
                }
                for note in self.read_for_prompt(current_turn=current_turn)
            ],
        }


def format_short_memory_prompt(notes: list[ShortMemoryNote]) -> str:
    if not notes:
        return ""

    lines = "\n".join(f"- {note.text}" for note in notes)
    return (
        "SHORT WORKING MEMORY\n"
        "These are recent working notes extracted from previous turns.\n"
        "They are not permanent facts. Use them only when relevant.\n\n"
        f"{lines}"
    )


def propose_short_memory_notes(
    *,
    user_text: str,
    reply_text: str,
    current_turn: int,
    default_ttl_turns: int = DEFAULT_SHORT_MEMORY_TTL_TURNS,
) -> ShortMemoryProposalResult:
    del reply_text
    normalized = _normalize_text(user_text)
    if not _should_capture(normalized):
        return ShortMemoryProposalResult(proposals=[])

    return ShortMemoryProposalResult(
        proposals=[
            ShortMemoryNote(
                kind=_classify_note_kind(normalized),
                text=normalized,
                confidence=0.65,
                importance=0.65,
                created_turn=current_turn,
                expires_after_turns=default_ttl_turns,
                created_at=datetime.now(UTC),
            )
        ]
    )


def _replace_note_id(note: ShortMemoryNote, note_id: str) -> ShortMemoryNote:
    return ShortMemoryNote(
        kind=note.kind,
        text=note.text,
        confidence=note.confidence,
        importance=note.importance,
        created_turn=note.created_turn,
        expires_after_turns=note.expires_after_turns,
        created_at=note.created_at,
        note_id=note_id,
    )


def _remaining_turns(note: ShortMemoryNote, *, current_turn: int) -> int:
    used_turns = max(0, current_turn - note.created_turn)
    return max(0, note.expires_after_turns - used_turns)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _should_capture(text: str) -> bool:
    if len(text) < 8:
        return False
    return any(cue in text for cue in _WORKING_CONTEXT_CUES)


def _classify_note_kind(
    text: str,
) -> Literal["working_context", "short_intent", "next_trial"]:
    if "DB" in text or "永続化" in text or "memory" in text or "メモ" in text:
        return "working_context"
    if "次" in text or "あとで" in text or "試したい" in text:
        return "next_trial"
    if "したい" in text or "してほしい" in text or "優先" in text:
        return "short_intent"
    return "working_context"
