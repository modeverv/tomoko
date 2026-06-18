from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from server.shared.candidate import (
    InMemoryCandidateStore,
    InMemoryPregeneratedAudioChunkStore,
    PregeneratedAudioChunk,
    UtteranceCandidate,
)


@pytest.mark.unit
async def test_maturity2_requires_generated_text_and_audio() -> None:
    now = datetime(2026, 5, 24, 23, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="maturity=2 requires"):
        UtteranceCandidate(
            id=uuid4(),
            seed="seed",
            generated_text="言えるよ",
            generated_audio=None,
            priority=0.8,
            urgent=False,
            created_at=now,
            expires_at=now + timedelta(minutes=5),
            spoken_at=None,
            dismissed_at=None,
            maturity=2,
            source="unit",
        )


@pytest.mark.unit
async def test_in_memory_candidate_store_round_trips_generated_audio() -> None:
    now = datetime(2026, 5, 24, 23, 0, tzinfo=UTC)
    audio = b"RIFF\x24\x00\x00\x00WAVEfmt cached"
    store = InMemoryCandidateStore()
    candidate = await store.insert_utterance_candidate(
        seed="休憩",
        source="unit",
        expires_at=now + timedelta(minutes=10),
        maturity=1,
        generated_text="少し休もう。",
        created_at=now,
    )

    await store.mark_utterance_pregenerated(candidate.id, generated_audio=audio)
    active = await store.fetch_active_utterance_candidates(now=now, limit=1)

    assert active[0].id == candidate.id
    assert active[0].maturity == 2
    assert active[0].generated_text == "少し休もう。"
    assert active[0].generated_audio == audio


@pytest.mark.unit
async def test_pregenerated_audio_chunk_store_replaces_and_orders_chunks() -> None:
    now = datetime(2026, 5, 24, 23, 0, tzinfo=UTC)
    candidate_id = uuid4()
    store = InMemoryPregeneratedAudioChunkStore()

    inserted = await store.replace_chunks(
        candidate_id,
        (b"RIFF-one", b"RIFF-two"),
        created_at=now,
    )
    assert [chunk.chunk_index for chunk in inserted] == [0, 1]
    assert [chunk.is_last for chunk in inserted] == [False, True]

    await store.replace_chunks(candidate_id, (b"RIFF-replaced",), created_at=now)
    fetched = await store.fetch_chunks(candidate_id)

    assert len(fetched) == 1
    assert fetched[0].audio_data == b"RIFF-replaced"
    assert fetched[0].audio_format == "riff_wave"
    assert fetched[0].is_last is True


@pytest.mark.unit
def test_pregenerated_audio_chunk_from_db_row_preserves_bytes() -> None:
    now = datetime(2026, 5, 24, 23, 0, tzinfo=UTC)
    row = (
        uuid4(),
        uuid4(),
        1,
        memoryview(b"RIFF-db"),
        "riff_wave",
        True,
        now,
    )

    chunk = PregeneratedAudioChunk.from_db_row(row)

    assert chunk.chunk_index == 1
    assert chunk.audio_data == b"RIFF-db"
    assert chunk.audio_format == "riff_wave"
    assert chunk.is_last is True
