from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from server.session_memory_helpers import (
    context_snapshot_long_term_memory,
    session_summary_hit_to_memory,
)
from server.shared.models import (
    ContextBuildTrace,
    MemoryHit,
    SessionSummaryHit,
    TomokoContextSnapshot,
)


def _summary_hit(*, ended_at: datetime | None) -> SessionSummaryHit:
    return SessionSummaryHit(
        session_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        summary_text="著作権の話をした",
        started_at=datetime(2026, 5, 29, 9, 0, tzinfo=UTC),
        ended_at=ended_at,
        similarity=0.8125,
    )


def _context_snapshot(
    *,
    session_summaries: list[SessionSummaryHit] | None = None,
    memory_hits: list[MemoryHit] | None = None,
) -> TomokoContextSnapshot:
    return TomokoContextSnapshot(
        depth="fast",
        recent_turns=[],
        session_summaries=session_summaries or [],
        memory_hits=memory_hits or [],
        lexicon_terms=[],
        persona_slice=None,
        token_budget_hint=0,
        build_elapsed_ms=0.0,
        source_counts={},
        trace=ContextBuildTrace(
            budget_ms=0,
            elapsed_ms=0.0,
            timed_out=False,
            depth="fast",
            included_counts={},
            skipped_sources=[],
            stage_timings_ms={},
            cache_hits={},
            source_errors={},
        ),
    )


def _memory_hit(text: str) -> MemoryHit:
    return MemoryHit(
        speaker="user",
        text=text,
        timestamp=datetime(2026, 5, 29, 10, 0, tzinfo=UTC),
        similarity=0.7,
        source_id=f"turn:{text}",
    )


@pytest.mark.unit
def test_session_summary_hit_to_memory_preserves_prompt_payload_shape() -> None:
    ended_at = datetime(2026, 5, 29, 9, 30, tzinfo=UTC)

    memory = session_summary_hit_to_memory(_summary_hit(ended_at=ended_at))

    assert memory.speaker == "tomoko"
    assert memory.text == "会話セッション要約: 著作権の話をした"
    assert memory.timestamp == ended_at
    assert memory.similarity == 0.8125
    assert memory.emotion is None
    assert memory.source_id == "session_summary:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


@pytest.mark.unit
def test_session_summary_hit_to_memory_uses_started_at_when_ended_at_is_missing() -> None:
    hit = _summary_hit(ended_at=None)

    memory = session_summary_hit_to_memory(hit)

    assert memory.timestamp == hit.started_at
    assert memory.text == "会話セッション要約: 著作権の話をした"
    assert memory.source_id == "session_summary:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


@pytest.mark.unit
def test_context_snapshot_long_term_memory_keeps_session_summaries_first() -> None:
    summary = _summary_hit(ended_at=datetime(2026, 5, 29, 9, 30, tzinfo=UTC))
    turn_memory = _memory_hit("前にカレーの話をした")

    memories = context_snapshot_long_term_memory(
        _context_snapshot(
            session_summaries=[summary],
            memory_hits=[turn_memory],
        )
    )

    assert [memory.text for memory in memories] == [
        "会話セッション要約: 著作権の話をした",
        "前にカレーの話をした",
    ]
    assert memories[0].source_id == (
        "session_summary:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    )
    assert memories[1] is turn_memory


@pytest.mark.unit
def test_context_snapshot_long_term_memory_returns_empty_when_sources_are_empty() -> None:
    assert context_snapshot_long_term_memory(_context_snapshot()) == []
