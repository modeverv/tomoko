from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from server.shared.candidate import InMemoryCandidateStore, ThinkerSourceContext
from server.thinker.selection.highest import HighestPriority
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
