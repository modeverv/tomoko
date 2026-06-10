from __future__ import annotations

from datetime import datetime

from server.shared.models import MemoryHit


def format_long_term_memory_prompt(memories: list[MemoryHit]) -> str:
    if not memories:
        return ""

    formatted_memories = "\n".join(_format_memory(memory) for memory in memories)
    return (
        "## MEMORIES\n"
        f"{formatted_memories}"
    )


def _format_memory(memory: MemoryHit) -> str:
    if _is_calendar_memory(memory):
        return f"- {_format_calendar_memory_text(memory.text)}"

    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Tokyo")
    local_ts = memory.timestamp.astimezone(tz) if memory.timestamp.tzinfo is not None else memory.timestamp
    timestamp = local_ts.strftime("%m/%d %H:%M")
    speaker = _format_speaker(memory)
    return f"- [{timestamp}] {speaker}: {memory.text}"


def _format_speaker(memory: MemoryHit) -> str:
    return "ユーザー" if memory.speaker == "user" else "トモコ"


def _is_calendar_memory(memory: MemoryHit) -> bool:
    return bool(memory.source_id and memory.source_id.startswith("calendar:"))


def _format_calendar_memory_text(text: str) -> str:
    return text.removeprefix("カレンダー予定: ").strip()


def _format_timestamp(timestamp: datetime) -> str:
    return timestamp.isoformat(timespec="seconds")
