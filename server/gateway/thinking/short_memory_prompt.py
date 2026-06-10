from __future__ import annotations

import re

from server.shared.models import ShortMemoryNote


def format_short_memory_prompt(notes: list[ShortMemoryNote]) -> str:
    if not notes:
        return ""

    unique_notes: dict[tuple[str, str], ShortMemoryNote] = {}
    for note in notes:
        unique_notes[_dedupe_key(note)] = note
    lines = "\n".join(_format_note_for_prompt(note) for note in unique_notes.values())
    return (
        "## SHORT MEMORY\n"
        f"{lines}"
    )


def _dedupe_key(note: ShortMemoryNote) -> tuple[str, str]:
    return (note.kind, _normalize_text(note.text).casefold())


def _format_note_for_prompt(note: ShortMemoryNote) -> str:
    if note.kind == "verbatim":
        return f"- 暗記: {note.text}"
    return f"- {note.text}"


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
