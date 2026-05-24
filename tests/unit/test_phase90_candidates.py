from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from server.shared.candidate import (
    ArrivalCandidate,
    ArrivalContextSnapshot,
    InMemoryCandidateStore,
    UtteranceCandidate,
)


@pytest.mark.unit
def test_arrival_context_snapshot_round_trips_json() -> None:
    computed_at = datetime(2026, 5, 24, 8, 30, tzinfo=UTC)

    snapshot = ArrivalContextSnapshot.from_json(
        {
            "schema_version": 1,
            "device_id": "kitchen",
            "computed_at": computed_at.isoformat(),
            "local_time": "08:30",
            "time_since_last_session_sec": 1200,
            "session_count_today": 2,
            "urgent_candidate_count": 1,
            "top_urgent_seeds": ["洗濯物を取り込む"],
            "persona_hint": "短く声をかける",
            "notes": ["まだ話しかけていない"],
        }
    )

    assert ArrivalContextSnapshot.from_json(snapshot.to_json()) == snapshot
    assert snapshot.top_urgent_seeds == ("洗濯物を取り込む",)


@pytest.mark.unit
def test_candidate_dtos_reject_invalid_enums() -> None:
    now = datetime.now(UTC)

    with pytest.raises(ValueError, match="Unsupported candidate maturity"):
        UtteranceCandidate(
            id=uuid4(),
            seed="買い物リストを思い出す",
            generated_text=None,
            generated_audio=None,
            priority=0.5,
            urgent=False,
            created_at=now,
            expires_at=now + timedelta(minutes=5),
            spoken_at=None,
            dismissed_at=None,
            maturity=9,
            source="unit",
            context_tags=(),
        )

    with pytest.raises(ValueError, match="Unsupported arrival behavior"):
        ArrivalCandidate(
            id=uuid4(),
            computed_at=now,
            valid_until=now + timedelta(minutes=3),
            context_snapshot=ArrivalContextSnapshot(
                device_id="kitchen",
                computed_at=now,
                local_time="12:00",
            ),
            behavior="wave",
            utterance_text=None,
            utterance_audio=None,
            used_at=None,
        )


@pytest.mark.unit
async def test_candidate_store_filters_and_orders_active_utterance_candidates() -> None:
    now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
    store = InMemoryCandidateStore()

    older_high = await store.insert_utterance_candidate(
        seed="昨日のカレーの続きを聞く",
        source="unit",
        priority=0.9,
        created_at=now - timedelta(minutes=2),
        expires_at=now + timedelta(minutes=5),
    )
    newer_high = await store.insert_utterance_candidate(
        seed="洗濯物を取り込むか聞く",
        source="unit",
        priority=0.9,
        created_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=5),
    )
    low = await store.insert_utterance_candidate(
        seed="軽い相槌候補",
        source="unit",
        priority=0.2,
        created_at=now - timedelta(minutes=3),
        expires_at=now + timedelta(minutes=5),
    )
    expired = await store.insert_utterance_candidate(
        seed="期限切れ候補",
        source="unit",
        priority=1.0,
        created_at=now - timedelta(minutes=4),
        expires_at=now - timedelta(seconds=1),
    )
    spoken = await store.insert_utterance_candidate(
        seed="もう話した候補",
        source="unit",
        priority=1.0,
        created_at=now - timedelta(minutes=4),
        expires_at=now + timedelta(minutes=5),
    )
    dismissed = await store.insert_utterance_candidate(
        seed="もう日記材料に回った候補",
        source="unit",
        priority=1.0,
        created_at=now - timedelta(minutes=4),
        expires_at=now + timedelta(minutes=5),
    )

    await store.mark_utterance_spoken(spoken.id, spoken_at=now)
    dismissed_count = await store.mark_expired_utterance_candidates(now)
    assert dismissed_count == 1
    await store.dismiss_utterance_candidate(dismissed.id, dismissed_at=now)

    active = await store.fetch_active_utterance_candidates(now=now, limit=10)

    assert [candidate.id for candidate in active] == [
        older_high.id,
        newer_high.id,
        low.id,
    ]
    assert expired.id not in {candidate.id for candidate in active}


@pytest.mark.unit
async def test_candidate_store_fetches_latest_fresh_arrival_candidate() -> None:
    now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
    store = InMemoryCandidateStore()

    stale = await store.insert_arrival_candidate(
        context_snapshot=ArrivalContextSnapshot(
            device_id="kitchen",
            computed_at=now,
            local_time="12:00",
        ),
        behavior="wait_silent",
        computed_at=now - timedelta(minutes=10),
        valid_until=now - timedelta(seconds=1),
    )
    older = await store.insert_arrival_candidate(
        context_snapshot=ArrivalContextSnapshot(
            device_id="kitchen",
            computed_at=now,
            local_time="12:00",
        ),
        behavior="subtle_react",
        computed_at=now - timedelta(minutes=2),
        valid_until=now + timedelta(minutes=1),
    )
    latest = await store.insert_arrival_candidate(
        context_snapshot=ArrivalContextSnapshot(
            device_id="kitchen",
            computed_at=now,
            local_time="12:00",
        ),
        behavior="speak_first",
        computed_at=now - timedelta(seconds=30),
        valid_until=now + timedelta(minutes=2),
        utterance_text="おかえり。今ちょうど考えてた。",
    )
    other_device = await store.insert_arrival_candidate(
        context_snapshot=ArrivalContextSnapshot(
            device_id="living",
            computed_at=now,
            local_time="12:00",
        ),
        behavior="speak_first",
        computed_at=now,
        valid_until=now + timedelta(minutes=2),
    )
    await store.mark_arrival_used(older.id, used_at=now)

    found = await store.fetch_latest_fresh_arrival_candidate(
        now=now,
        device_id="kitchen",
    )
    assert found == latest
    assert found != stale
    assert found != other_device

    any_device = await store.fetch_latest_fresh_arrival_candidate(
        now=now,
        device_id=None,
    )
    assert any_device == other_device
