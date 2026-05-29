from __future__ import annotations

from datetime import datetime

from server.shared.models import MemoryHit


def format_long_term_memory_prompt(memories: list[MemoryHit]) -> str:
    if not memories:
        return ""

    formatted_memories = "\n".join(_format_memory(memory) for memory in memories)
    return (
        "長期コンテキストとして関連しそうな過去会話や参照情報を渡します。"
        "必要な時だけ自然に使い、断定しすぎず、短く返答してください。\n"
        f"{formatted_memories}"
    )


def _format_memory(memory: MemoryHit) -> str:
    timestamp = _format_timestamp(memory.timestamp)
    speaker = _format_speaker(memory)
    emotion = f", emotion={memory.emotion}" if memory.emotion else ""
    return (
        f"- [{timestamp}] {speaker}: {memory.text} "
        f"(similarity={memory.similarity:.3f}{emotion})"
    )


def _format_speaker(memory: MemoryHit) -> str:
    if memory.source_id and memory.source_id.startswith("calendar:"):
        return "参照情報"
    return "ユーザー" if memory.speaker == "user" else "トモコ"


def _format_timestamp(timestamp: datetime) -> str:
    return timestamp.isoformat(timespec="seconds")
