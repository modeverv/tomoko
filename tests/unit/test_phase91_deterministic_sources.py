from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from server.shared.calendar import InMemoryCalendarEventStore
from server.shared.candidate import InMemoryCandidateStore, ThinkerSourceContext
from server.shared.models import CalendarEvent
from server.thinker.selection.highest import HighestPriority
from server.thinker.sources.calendar_reminder import CalendarReminderSource
from server.thinker.sources.time_based import TimeBasedSource


@pytest.mark.unit
async def test_time_based_source_is_deterministic_for_same_observed_at() -> None:
    observed_at = datetime(2026, 5, 24, 8, 30, tzinfo=UTC)
    context = ThinkerSourceContext(observed_at=observed_at)
    source = TimeBasedSource()

    first = await source.collect(context)
    second = await source.collect(context)

    assert first == second
    assert len(first) == 1
    assert first[0].source == "time_based"
    assert first[0].dedupe_key == "time_based:morning:2026-05-24"
    assert first[0].context_tags == (
        "dedupe:time_based:morning:2026-05-24",
        "time_of_day:morning",
    )


@pytest.mark.unit
async def test_time_based_source_changes_bucket_by_hour() -> None:
    source = TimeBasedSource()

    morning = (
        await source.collect(
            ThinkerSourceContext(
                observed_at=datetime(2026, 5, 24, 8, 0, tzinfo=UTC),
            )
        )
    )[0]
    night = (
        await source.collect(
            ThinkerSourceContext(
                observed_at=datetime(2026, 5, 24, 21, 0, tzinfo=UTC),
            )
        )
    )[0]

    assert morning.seed_text != night.seed_text
    assert morning.dedupe_key != night.dedupe_key


@pytest.mark.unit
async def test_calendar_reminder_source_generates_fifteen_minute_seed() -> None:
    now = datetime(2026, 6, 16, 10, 45, tzinfo=UTC)
    event = _calendar_event(
        summary="設計レビュー",
        start_time=now + timedelta(minutes=15),
        location="Zoom",
    )
    store = InMemoryCalendarEventStore()
    await store.replace_source_events(source_id="gcal", events=[event])

    seeds = await CalendarReminderSource(calendar_store=store).collect(
        ThinkerSourceContext(observed_at=now)
    )

    assert len(seeds) == 1
    seed = seeds[0]
    assert seed.source == "calendar_reminder"
    assert seed.priority == 0.65
    assert seed.urgent is False
    assert seed.expires_at == event.start_time - timedelta(minutes=5)
    assert seed.dedupe_key == (
        "calendar_reminder:gcal:evt-1:2026-06-16T11:00:00+00:00:"
        "fifteen_min"
    )
    assert seed.context_tags == (
        "dedupe:calendar_reminder:gcal:evt-1:2026-06-16T11:00:00+00:00:"
        "fifteen_min",
        "calendar",
        "calendar_window:fifteen_min",
    )
    assert "予定「設計レビュー」が15分後に始まる" in seed.seed_text
    assert seed.metadata_json["available_at"] == now.isoformat()
    assert seed.metadata_json["event_location"] == "Zoom"


@pytest.mark.unit
async def test_calendar_reminder_source_rolls_windows_forward() -> None:
    start = datetime(2026, 6, 16, 11, 0, tzinfo=UTC)
    store = InMemoryCalendarEventStore()
    await store.replace_source_events(
        source_id="gcal",
        events=[_calendar_event(summary="ペア作業", start_time=start)],
    )
    source = CalendarReminderSource(calendar_store=store)

    before = await source.collect(
        ThinkerSourceContext(observed_at=start - timedelta(minutes=16))
    )
    five_min = await source.collect(
        ThinkerSourceContext(observed_at=start - timedelta(minutes=4, seconds=59))
    )
    due = await source.collect(ThinkerSourceContext(observed_at=start))
    expired = await source.collect(
        ThinkerSourceContext(observed_at=start + timedelta(minutes=10))
    )

    assert before == []
    assert five_min[0].metadata_json["reminder_window"] == "five_min"
    assert five_min[0].urgent is True
    assert five_min[0].expires_at == start
    assert due[0].metadata_json["reminder_window"] == "due"
    assert due[0].expires_at == start + timedelta(minutes=10)
    assert expired == []


@pytest.mark.unit
async def test_calendar_reminder_seed_dedupe_skips_active_duplicate() -> None:
    now = datetime(2026, 6, 16, 10, 45, tzinfo=UTC)
    event = _calendar_event(
        summary="設計レビュー",
        start_time=now + timedelta(minutes=15),
    )
    calendar_store = InMemoryCalendarEventStore()
    await calendar_store.replace_source_events(source_id="gcal", events=[event])
    candidate_store = InMemoryCandidateStore()
    source = CalendarReminderSource(calendar_store=calendar_store)

    seed = (await source.collect(ThinkerSourceContext(observed_at=now)))[0]
    first = await candidate_store.insert_seed_candidate_once(seed, created_at=now)
    second = await candidate_store.insert_seed_candidate_once(
        seed,
        created_at=now + timedelta(seconds=30),
    )

    assert first is not None
    assert second is None
    assert first.expires_at == event.start_time - timedelta(minutes=5)


@pytest.mark.unit
async def test_calendar_reminder_source_skips_all_day_and_cancelled_events() -> None:
    now = datetime(2026, 6, 16, 10, 45, tzinfo=UTC)
    store = InMemoryCalendarEventStore()
    await store.replace_source_events(
        source_id="gcal",
        events=[
            _calendar_event(
                summary="終日予定",
                start_time=now + timedelta(minutes=15),
                all_day=True,
            ),
            _calendar_event(
                summary="キャンセル予定",
                start_time=now + timedelta(minutes=15),
                status="cancelled",
            ),
        ],
    )

    seeds = await CalendarReminderSource(calendar_store=store).collect(
        ThinkerSourceContext(observed_at=now)
    )

    assert seeds == []


@pytest.mark.unit
async def test_store_skips_active_candidate_with_same_dedupe_key() -> None:
    now = datetime(2026, 5, 24, 8, 30, tzinfo=UTC)
    store = InMemoryCandidateStore()
    seed = (await TimeBasedSource().collect(ThinkerSourceContext(observed_at=now)))[0]

    first = await store.insert_seed_candidate_once(seed, created_at=now)
    second = await store.insert_seed_candidate_once(
        seed,
        created_at=now + timedelta(seconds=1),
    )

    assert first is not None
    assert second is None
    assert len(await store.fetch_active_utterance_candidates(now=now, limit=10)) == 1

    await store.mark_utterance_spoken(first.id, spoken_at=now + timedelta(seconds=2))
    third = await store.insert_seed_candidate_once(
        seed,
        created_at=now + timedelta(seconds=3),
    )

    assert third is not None


@pytest.mark.unit
async def test_highest_priority_tie_break_is_stable() -> None:
    now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
    store = InMemoryCandidateStore()

    later_expiry = await store.insert_utterance_candidate(
        seed="priority は同じだが期限が遅い",
        source="unit",
        priority=0.8,
        urgent=True,
        created_at=now - timedelta(minutes=3),
        expires_at=now + timedelta(minutes=10),
    )
    earlier_expiry = await store.insert_utterance_candidate(
        seed="priority と urgent が同じで期限が早い",
        source="unit",
        priority=0.8,
        urgent=True,
        created_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=3),
    )
    non_urgent = await store.insert_utterance_candidate(
        seed="priority は同じだが urgent ではない",
        source="unit",
        priority=0.8,
        urgent=False,
        created_at=now - timedelta(minutes=4),
        expires_at=now + timedelta(minutes=1),
    )
    older_low = await store.insert_utterance_candidate(
        seed="priority が低い",
        source="unit",
        priority=0.3,
        urgent=True,
        created_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(minutes=1),
    )

    candidates = [older_low, non_urgent, later_expiry, earlier_expiry]

    assert HighestPriority().select(candidates) == earlier_expiry


def _calendar_event(
    *,
    summary: str,
    start_time: datetime,
    location: str = "",
    all_day: bool = False,
    status: str = "confirmed",
) -> CalendarEvent:
    return CalendarEvent(
        source_id="gcal",
        uid="evt-1",
        summary=summary,
        start_time=start_time,
        end_time=start_time + timedelta(hours=1),
        all_day=all_day,
        location=location,
        status=status,
    )
