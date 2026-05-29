from __future__ import annotations

from server.shared.models import ShortMemoryNote


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
