from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from server.shared.candidate import InMemoryCandidateStore, ThinkerSourceContext
from server.shared.models import UserContextSnapshot
from server.shared.perception import InMemoryUserContextSnapshotStore
from server.thinker.sources.context_snapshot import (
    ActivityContextSource,
    ScreenContextSource,
)


@pytest.mark.unit
async def test_screen_context_source_generates_help_candidate_for_debugging() -> None:
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    store = InMemoryUserContextSnapshotStore()
    await store.insert_snapshot(
        _snapshot(
            now=now,
            screen_activity_label="debugging tests",
            interaction_readiness="needs_help_maybe",
        )
    )

    seeds = await ScreenContextSource(snapshot_store=store).collect(
        ThinkerSourceContext(observed_at=now)
    )

    assert len(seeds) == 1
    seed = seeds[0]
    assert seed.source == "screen_context"
    assert seed.priority == 0.72
    assert seed.urgent is False
    assert seed.expires_at == now + timedelta(minutes=20)
    assert seed.dedupe_key == "screen_context:2026-06-16T10:00:00+00:00:debugging-tests"
    assert "画面では debugging tests が続いている" in seed.seed_text
    assert "readiness:needs_help_maybe" in seed.context_tags
    assert seed.metadata_json["intrusion"] == "low"


@pytest.mark.unit
async def test_activity_context_source_generates_chat_candidate_when_idle() -> None:
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    store = InMemoryUserContextSnapshotStore()
    await store.insert_snapshot(
        _snapshot(
            now=now,
            activity_label="idle",
            interaction_readiness="chat_ok",
        )
    )

    seed = (
        await ActivityContextSource(snapshot_store=store).collect(
            ThinkerSourceContext(observed_at=now)
        )
    )[0]

    assert seed.source == "activity_context"
    assert seed.priority == 0.55
    assert seed.urgent is False
    assert "今は idle で、軽く話しかけてもよさそう" in seed.seed_text
    assert "readiness:chat_ok" in seed.context_tags


@pytest.mark.unit
async def test_context_sources_suppress_away_and_do_not_disturb() -> None:
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    store = InMemoryUserContextSnapshotStore()
    await store.insert_snapshot(
        _snapshot(
            now=now,
            activity_label="away",
            screen_activity_label="watching video",
            interaction_readiness="away",
        )
    )

    assert await ScreenContextSource(snapshot_store=store).collect(
        ThinkerSourceContext(observed_at=now)
    ) == []
    assert await ActivityContextSource(snapshot_store=store).collect(
        ThinkerSourceContext(observed_at=now)
    ) == []


@pytest.mark.unit
async def test_context_candidate_dedupe_uses_snapshot_time_and_label() -> None:
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    snapshot_store = InMemoryUserContextSnapshotStore()
    candidate_store = InMemoryCandidateStore()
    await snapshot_store.insert_snapshot(
        _snapshot(
            now=now,
            screen_activity_label="debugging tests",
            interaction_readiness="needs_help_maybe",
        )
    )
    seed = (
        await ScreenContextSource(snapshot_store=snapshot_store).collect(
            ThinkerSourceContext(observed_at=now)
        )
    )[0]

    first = await candidate_store.insert_seed_candidate_once(seed, created_at=now)
    second = await candidate_store.insert_seed_candidate_once(
        seed,
        created_at=now + timedelta(seconds=5),
    )

    assert first is not None
    assert second is None


def _snapshot(
    *,
    now: datetime,
    interaction_readiness: str,
    activity_label: str | None = None,
    screen_activity_label: str | None = None,
) -> UserContextSnapshot:
    return UserContextSnapshot(
        computed_at=now,
        present=True,
        activity_label=activity_label,
        screen_activity_label=screen_activity_label,
        user_activity_summary="unit",
        context_summary="unit",
        interaction_readiness=interaction_readiness,  # type: ignore[arg-type]
        confidence=0.8,
    )
