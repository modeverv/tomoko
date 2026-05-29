from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from server.session_memory_helpers import (
    calendar_event_to_memory,
    context_snapshot_calendar_memory,
    context_snapshot_long_term_memory,
    session_summary_hit_to_memory,
)
from server.shared.models import (
    CalendarEvent,
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
    calendar_events: list[CalendarEvent] | None = None,
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
        calendar_events=calendar_events or [],
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


def _calendar_event(
    summary: str,
    *,
    status: str = "confirmed",
) -> CalendarEvent:
    return CalendarEvent(
        source_id="gcal",
        uid=f"{summary}@example.com",
        summary=summary,
        start_time=datetime(2026, 5, 30, 4, 0, tzinfo=UTC),
        end_time=datetime(2026, 5, 30, 5, 0, tzinfo=UTC),
        all_day=False,
        location="Kitchen",
        status=status,
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


@pytest.mark.unit
def test_calendar_event_to_memory_preserves_reference_payload_shape() -> None:
    memory = calendar_event_to_memory(_calendar_event("家族の予定"))

    assert memory.speaker == "tomoko"
    assert memory.text == "カレンダー予定: 2026-05-30 13:00-14:00: 家族の予定 @ Kitchen"
    assert memory.timestamp == datetime(2026, 5, 30, 4, 0, tzinfo=UTC)
    assert memory.similarity == 1.0
    assert memory.source_id == (
        "calendar:gcal:家族の予定@example.com:2026-05-30T04:00:00+00:00"
    )


@pytest.mark.unit
def test_context_snapshot_calendar_memory_skips_cancelled_events() -> None:
    memories = context_snapshot_calendar_memory(
        _context_snapshot(
            calendar_events=[
                _calendar_event("家族の予定"),
                _calendar_event("キャンセルされた予定", status="cancelled"),
            ]
        )
    )

    assert [memory.text for memory in memories] == [
        "カレンダー予定: 2026-05-30 13:00-14:00: 家族の予定 @ Kitchen"
    ]
