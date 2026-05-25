from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from server.shared.diary import DiaryEntry, InMemoryDiaryStore


@pytest.mark.unit
def test_diary_entry_rejects_empty_body_and_unknown_schema() -> None:
    with pytest.raises(ValueError, match="body_text"):
        DiaryEntry(id=uuid4(), diary_date=date(2026, 5, 24), body_text="")

    with pytest.raises(ValueError, match="schema_version"):
        DiaryEntry(
            id=uuid4(),
            diary_date=date(2026, 5, 24),
            body_text="今日は静かだった。",
            schema_version=2,
        )


@pytest.mark.unit
async def test_in_memory_diary_store_round_trip_and_recent_order() -> None:
    store = InMemoryDiaryStore()
    session_id = uuid4()
    candidate_id = uuid4()
    older = await store.insert_entry(
        diary_date=date(2026, 5, 23),
        body_text="昨日はよく話した。",
        created_at=datetime(2026, 5, 23, 23, 0, tzinfo=UTC),
    )
    latest = await store.insert_entry(
        diary_date=date(2026, 5, 24),
        body_text="今日は言えなかったことが少し残った。",
        source_session_ids=(session_id,),
        source_candidate_ids=(candidate_id,),
        mood="quiet",
        created_at=datetime(2026, 5, 24, 23, 0, tzinfo=UTC),
    )
    middle = await store.insert_entry(
        diary_date=date(2026, 5, 24),
        body_text="朝は短い会話だけだった。",
        created_at=datetime(2026, 5, 24, 8, 0, tzinfo=UTC),
    )

    recent = await store.fetch_recent_entries(limit=2)

    assert recent == [latest, middle]
    assert older not in recent
    assert latest.source_session_ids == (session_id,)
    assert latest.source_candidate_ids == (candidate_id,)
    assert latest.mood == "quiet"
    assert latest.diary_version == 1
    assert middle.diary_version == 2


@pytest.mark.unit
def test_diary_entry_from_db_row() -> None:
    entry_id = uuid4()
    session_id = uuid4()
    candidate_id = uuid4()
    world_observation_id = uuid4()
    created_at = datetime(2026, 5, 24, 23, 0, tzinfo=UTC)

    entry = DiaryEntry.from_db_row(
        (
            entry_id,
            date(2026, 5, 24),
            "今日は眠そうだった。",
            3,
            [session_id],
            [candidate_id],
            [world_observation_id],
            "sleepy",
            1,
            created_at + timedelta(seconds=1),
        )
    )

    assert entry.id == entry_id
    assert entry.diary_date == date(2026, 5, 24)
    assert entry.diary_version == 3
    assert entry.source_session_ids == (session_id,)
    assert entry.source_candidate_ids == (candidate_id,)
    assert entry.source_world_observation_interpretation_ids == (world_observation_id,)
    assert entry.mood == "sleepy"
