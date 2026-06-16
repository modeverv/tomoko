from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from server.shared.calendar import InMemoryCalendarEventStore
from server.shared.models import (
    CalendarEvent,
    WorldObservationInterpretationRecord,
)
from server.shared.perception import (
    InMemoryHumanActivityObservationStore,
    InMemoryHumanPresenceObservationStore,
    InMemoryScreenActivityObservationStore,
    InMemoryUserContextSnapshotStore,
)
from server.thinker.perception.context_snapshot import (
    UserContextSnapshotBuilder,
    decide_interaction_readiness,
)


@dataclass
class FakeWorldStore:
    records: tuple[WorldObservationInterpretationRecord, ...]

    async def fetch_candidate_interpretations(
        self,
        *,
        limit: int,
        min_confidence: float = 0.45,
        min_interest: float = 0.45,
    ) -> tuple[WorldObservationInterpretationRecord, ...]:
        del min_confidence, min_interest
        return self.records[:limit]


@pytest.mark.unit
async def test_user_context_snapshot_builder_merges_latest_sources() -> None:
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    frame_id = UUID("00000000-0000-0000-0000-000000000001")
    presence_store = InMemoryHumanPresenceObservationStore()
    activity_store = InMemoryHumanActivityObservationStore()
    screen_store = InMemoryScreenActivityObservationStore()
    calendar_store = InMemoryCalendarEventStore()
    snapshot_store = InMemoryUserContextSnapshotStore()

    presence = await presence_store.insert_observation(
        frame_id=frame_id,
        observed_at=now - timedelta(seconds=10),
        present=True,
        confidence=0.9,
        model="unit-presence",
    )
    activity = await activity_store.insert_observation(
        frame_id=frame_id,
        presence_observation_id=presence.id,
        observed_at=now - timedelta(seconds=8),
        activity_label="typing",
        confidence=0.8,
        model="unit-activity",
    )
    screen = await screen_store.insert_observation(
        frame_id=UUID("00000000-0000-0000-0000-000000000002"),
        observed_at=now - timedelta(seconds=5),
        screen_activity_label="debugging tests",
        app_hint="Terminal",
        document_hint="pytest",
        confidence=0.82,
        model="unit-screen",
    )
    await calendar_store.replace_source_events(
        source_id="gcal",
        events=[
            CalendarEvent(
                source_id="gcal",
                uid="event-1",
                summary="設計レビュー",
                start_time=now + timedelta(minutes=30),
                end_time=now + timedelta(hours=1),
                all_day=False,
            )
        ],
    )
    world_store = FakeWorldStore(
        records=(
            _world_record(
                title="MLX update",
                interpretation_text="MLX の新しい改善が出ている",
                created_at=now,
            ),
        )
    )
    builder = UserContextSnapshotBuilder(
        snapshot_store=snapshot_store,
        presence_store=presence_store,
        activity_store=activity_store,
        screen_store=screen_store,
        calendar_store=calendar_store,
        world_store=world_store,  # type: ignore[arg-type]
        device_id="desk",
    )

    result = await builder.build_once(now=now)
    snapshot = result.snapshot

    assert snapshot.present is True
    assert snapshot.activity_label == "typing"
    assert snapshot.screen_activity_label == "debugging tests"
    assert snapshot.calendar_summary == "10:30 設計レビュー"
    assert snapshot.world_summary == "MLX update: MLX の新しい改善が出ている"
    assert snapshot.user_activity_summary == "present; activity=typing; screen=debugging tests"
    assert snapshot.context_summary
    assert snapshot.interaction_readiness == "needs_help_maybe"
    assert snapshot.source_frame_ids == (frame_id, screen.frame_id)
    assert snapshot.source_observation_ids == (
        presence.id,
        activity.id,
        screen.id,
    )
    assert result.trace.source_counts == {
        "presence": 1,
        "activity": 1,
        "screen": 1,
        "calendar": 1,
        "world": 1,
    }
    assert result.trace.elapsed_ms >= 0
    assert (await snapshot_store.fetch_latest(limit=1))[0] == snapshot


@pytest.mark.unit
async def test_user_context_snapshot_builder_rounds_absent_activity_to_away() -> None:
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    frame_id = UUID("00000000-0000-0000-0000-000000000003")
    presence_store = InMemoryHumanPresenceObservationStore()
    activity_store = InMemoryHumanActivityObservationStore()
    snapshot_store = InMemoryUserContextSnapshotStore()
    await presence_store.insert_observation(
        frame_id=frame_id,
        observed_at=now,
        present=False,
        confidence=0.9,
        model="unit-presence",
    )
    await activity_store.insert_observation(
        frame_id=frame_id,
        observed_at=now,
        activity_label="typing",
        confidence=0.8,
        model="unit-activity",
    )

    result = await UserContextSnapshotBuilder(
        snapshot_store=snapshot_store,
        presence_store=presence_store,
        activity_store=activity_store,
    ).build_once(now=now)

    assert result.snapshot.present is False
    assert result.snapshot.activity_label == "away"
    assert result.snapshot.interaction_readiness == "away"


@pytest.mark.unit
def test_decide_interaction_readiness_rules() -> None:
    assert (
        decide_interaction_readiness(
            present=False,
            activity_label="typing",
            screen_activity_label="debugging tests",
        )
        == "away"
    )
    assert (
        decide_interaction_readiness(
            present=True,
            activity_label="playing guitar",
            screen_activity_label=None,
        )
        == "do_not_disturb"
    )
    assert (
        decide_interaction_readiness(
            present=True,
            activity_label="typing",
            screen_activity_label="debugging tests",
        )
        == "needs_help_maybe"
    )
    assert (
        decide_interaction_readiness(
            present=True,
            activity_label="idle",
            screen_activity_label=None,
        )
        == "chat_ok"
    )


@pytest.mark.unit
async def test_user_context_snapshot_builder_dedupes_world_summary() -> None:
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    snapshot_store = InMemoryUserContextSnapshotStore()
    world_store = FakeWorldStore(
        records=(
            _world_record(
                title="MLX update",
                interpretation_text="同じ話題",
                created_at=now,
            ),
            _world_record(
                title="MLX update",
                interpretation_text="同じ話題",
                created_at=now,
            ),
        )
    )

    result = await UserContextSnapshotBuilder(
        snapshot_store=snapshot_store,
        world_store=world_store,  # type: ignore[arg-type]
    ).build_once(now=now)

    assert result.snapshot.world_summary == "MLX update: 同じ話題"


def _world_record(
    *,
    title: str,
    interpretation_text: str,
    created_at: datetime,
) -> WorldObservationInterpretationRecord:
    item_id = uuid4()
    return WorldObservationInterpretationRecord(
        id=uuid4(),
        item_id=item_id,
        document_id=uuid4(),
        topic="ai",
        title=title,
        summary="summary",
        source_hint="unit",
        freshness="fresh",
        confidence=0.9,
        persona_state_version_id=None,
        persona_lexicon_version_id=None,
        relevance_to_user=0.8,
        tomoko_interest=0.8,
        emotional_tone="curious",
        memory_value=0.4,
        speakability_hint="low_intrusion",
        interpretation_text=interpretation_text,
        tomoko_private_reaction="",
        candidate_seed_text="",
        reason_json={},
        created_at=created_at,
    )
