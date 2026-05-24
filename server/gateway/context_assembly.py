from __future__ import annotations

from server.shared.models import ConversationTurn, MemoryHit


def assemble_recent_turns(
    *,
    same_session_turns: list[ConversationTurn],
    recent_turns: list[ConversationTurn],
    current_user_text: str,
    limit: int,
) -> list[ConversationTurn]:
    same_session = drop_current_user_turn(same_session_turns, current_user_text)
    if len(same_session) >= limit:
        return same_session[-limit:]
    recent = drop_current_user_turn(recent_turns, current_user_text)
    supplement_limit = limit - len(same_session)
    supplement = [
        turn
        for turn in recent
        if not same_context_turn_exists(turn, same_session)
    ][-supplement_limit:]
    return (supplement + same_session)[-limit:]


def filter_current_memory_hits(
    memory_hits: list[MemoryHit],
    *,
    current_user_text: str,
) -> list[MemoryHit]:
    return [
        memory
        for memory in memory_hits
        if not (memory.speaker == "user" and memory.text == current_user_text)
    ]


def drop_current_user_turn(
    turns: list[ConversationTurn],
    current_user_text: str,
) -> list[ConversationTurn]:
    if turns and turns[-1].speaker == "user" and turns[-1].text == current_user_text:
        return turns[:-1]
    return turns


def same_context_turn_exists(
    turn: ConversationTurn,
    others: list[ConversationTurn],
) -> bool:
    return any(
        other.speaker == turn.speaker
        and other.text == turn.text
        and other.timestamp == turn.timestamp
        for other in others
    )
