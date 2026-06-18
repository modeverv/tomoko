from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from server.session_carryover import (
    RetrievedContextCarryoverState,
    retrieved_context_key,
)
from server.shared.models import MemoryHit


def _memory(
    text: str,
    *,
    source_id: str | None = None,
    similarity: float = 0.9,
    seconds: int = 0,
) -> MemoryHit:
    return MemoryHit(
        speaker="user",
        text=text,
        timestamp=datetime(2026, 5, 29, 12, 0, tzinfo=UTC)
        + timedelta(seconds=seconds),
        similarity=similarity,
        source_id=source_id,
    )


@pytest.mark.unit
def test_retrieved_context_key_prefers_source_id() -> None:
    memory = _memory("  智子  のこと  ", source_id="session_summary:abc")

    assert retrieved_context_key(memory) == "session_summary:abc"


@pytest.mark.unit
def test_retrieved_context_key_normalizes_text_without_source_id() -> None:
    first = _memory("智子   のこと", source_id=None)
    second = _memory("智子 のこと", source_id=None)

    assert retrieved_context_key(first) == retrieved_context_key(second)


@pytest.mark.unit
def test_merge_carried_long_term_memory_keeps_fresh_first_and_dedups() -> None:
    state = RetrievedContextCarryoverState()
    old_same = _memory("古い同じ記憶", source_id="same", seconds=1)
    old_other = _memory("古い別記憶", source_id="other", seconds=2)
    state.remember([old_same, old_other])

    fresh_same = _memory("新しい同じ記憶", source_id="same", seconds=3)
    fresh_new = _memory("新しい記憶", source_id="new", seconds=4)
    result = state.merge_carried_long_term_memory([fresh_same, fresh_new])

    assert result.memories == [fresh_same, fresh_new, old_other]
    assert result.carried_count == 2
    assert result.fresh_count == 2
    assert result.merged_count == 3


@pytest.mark.unit
def test_remember_evicts_oldest_entry_when_entry_count_exceeds_limit() -> None:
    state = RetrievedContextCarryoverState()
    memories = [
        _memory(f"記憶{i}", source_id=f"id:{i}", similarity=0.5 + i / 10, seconds=i)
        for i in range(7)
    ]

    result = state.remember(memories)

    assert result is not None
    assert result.added == 7
    assert result.total == 6
    assert [eviction.reason for eviction in result.evicted] == ["entry_count"]
    assert result.evicted[0].key == "id:0"
    assert [entry.key for entry in state.entries] == [f"id:{i}" for i in range(1, 7)]


@pytest.mark.unit
def test_remember_evicts_by_text_budget_after_entry_count() -> None:
    state = RetrievedContextCarryoverState()
    first = _memory("a" * 500, source_id="long:1", seconds=1)
    second = _memory("b" * 500, source_id="long:2", seconds=2)

    result = state.remember([first, second])

    assert result is not None
    assert result.total == 1
    assert [eviction.reason for eviction in result.evicted] == ["text_budget"]
    assert result.evicted[0].key == "long:1"
    assert [entry.key for entry in state.entries] == ["long:2"]


@pytest.mark.unit
def test_carried_long_term_memory_updates_last_used_sequence_and_clear_returns_count() -> None:
    state = RetrievedContextCarryoverState()
    first = _memory("最初", source_id="first", seconds=1)
    second = _memory("次", source_id="second", seconds=2)
    state.remember([first, second])

    carried = state.carried_long_term_memory()
    last_used = {entry.last_used_seq for entry in state.entries}
    count = state.clear()

    assert carried == [first, second]
    assert len(last_used) == 1
    assert count == 2
    assert state.entries == []
