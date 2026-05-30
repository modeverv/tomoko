from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from server.shared.candidate import (
    ArrivalContextSnapshot,
    PostgresCandidateStore,
    PostgresPregeneratedAudioChunkStore,
)
from server.shared.config import NodeConfig


@pytest.mark.integration
async def test_postgres_candidate_store_round_trip() -> None:
    config = NodeConfig.load("config/central_realtime.toml")
    dsn = config.database.dsn
    candidate_ddl = "docker/postgres/init/006_candidates.sql"
    pregenerated_audio_ddl = "docker/postgres/init/009_pregenerated_audio_chunks.sql"

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(open(candidate_ddl, encoding="utf-8").read())
            await cur.execute(open(pregenerated_audio_ddl, encoding="utf-8").read())
            await cur.execute(
                """
                DELETE FROM pregenerated_audio_chunks
                WHERE utterance_candidate_id IN (
                    SELECT id
                    FROM utterance_candidates
                    WHERE source = 'integration'
                      AND 'phase90_integration' = ANY(context_tags)
                )
                """
            )
            await cur.execute(
                """
                DELETE FROM utterance_candidates
                WHERE source = 'integration'
                  AND 'phase90_integration' = ANY(context_tags)
                """
            )
            await cur.execute(
                """
                DELETE FROM arrival_candidates
                WHERE device_id = 'phase90-integration'
                """
            )
        await conn.commit()

    store = PostgresCandidateStore(dsn)
    audio_chunks = PostgresPregeneratedAudioChunkStore(dsn)
    now = datetime(2099, 5, 24, 12, 0, tzinfo=UTC)
    device_id = "phase90-integration"
    inserted_ids: list[object] = []
    arrival_ids: list[object] = []

    try:
        low = await store.insert_utterance_candidate(
            seed="軽い候補",
            source="integration",
            priority=0.1,
            created_at=now - timedelta(minutes=2),
            expires_at=now + timedelta(minutes=5),
            context_tags=("phase90_integration",),
        )
        high = await store.insert_utterance_candidate(
            seed="優先候補",
            source="integration",
            priority=0.9,
            created_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(minutes=5),
            generated_text="今なら少し話せそう。",
            maturity=1,
            context_tags=("phase90_integration", "priority"),
        )
        expired = await store.insert_utterance_candidate(
            seed="期限切れ候補",
            source="integration",
            priority=1.0,
            created_at=now - timedelta(minutes=3),
            expires_at=now - timedelta(seconds=1),
        )
        inserted_ids.extend([low.id, high.id, expired.id])

        dismissed_count = await store.mark_expired_utterance_candidates(now)
        assert dismissed_count >= 1

        active = await store.fetch_active_utterance_candidates(now=now, limit=1000)
        active = [candidate for candidate in active if candidate.id in {low.id, high.id}]
        active_ids = [candidate.id for candidate in active]
        assert active_ids[:2] == [high.id, low.id]
        assert expired.id not in active_ids
        assert active[0].generated_text == "今なら少し話せそう。"
        assert active[0].maturity == 1
        assert active[0].context_tags == ("phase90_integration", "priority")

        generated_audio = b"RIFF\x24\x00\x00\x00WAVEfmt integration"
        await store.mark_utterance_pregenerated(
            high.id,
            generated_audio=generated_audio,
        )
        active_after_pregeneration = await store.fetch_active_utterance_candidates(
            now=now,
            limit=1000,
        )
        pregenerated = next(
            candidate
            for candidate in active_after_pregeneration
            if candidate.id == high.id
        )
        assert pregenerated.maturity == 2
        assert pregenerated.generated_audio == generated_audio

        inserted_chunks = await audio_chunks.replace_chunks(
            high.id,
            (b"RIFF-part-1", b"RIFF-part-2"),
            created_at=now,
        )
        fetched_chunks = await audio_chunks.fetch_chunks(high.id)
        assert tuple(chunk.audio_data for chunk in inserted_chunks) == (
            b"RIFF-part-1",
            b"RIFF-part-2",
        )
        assert tuple(chunk.audio_data for chunk in fetched_chunks) == (
            b"RIFF-part-1",
            b"RIFF-part-2",
        )
        assert fetched_chunks[-1].is_last is True

        await store.mark_utterance_spoken(high.id, spoken_at=now)
        active_after_spoken = await store.fetch_active_utterance_candidates(
            now=now,
            limit=10,
        )
        assert high.id not in {candidate.id for candidate in active_after_spoken}

        arrival = await store.insert_arrival_candidate(
            context_snapshot=ArrivalContextSnapshot(
                device_id=device_id,
                computed_at=now,
                local_time="12:00",
                session_count_today=2,
                persona_hint="昼前に買い物の話をした",
            ),
            behavior="speak_first",
            computed_at=now,
            valid_until=now + timedelta(minutes=3),
            utterance_text="戻ってきた。さっきの買い物の話、続ける？",
        )
        arrival_ids.append(arrival.id)

        fresh = await store.fetch_latest_fresh_arrival_candidate(
            now=now,
            device_id=device_id,
        )
        assert fresh == arrival
        assert fresh.context_snapshot.persona_hint == "昼前に買い物の話をした"

        await store.mark_arrival_used(arrival.id, used_at=now)
        assert (
            await store.fetch_latest_fresh_arrival_candidate(
                now=now,
                device_id=device_id,
            )
            is None
        )

        cleanup_base = datetime(2000, 1, 8, 12, 0, tzinfo=UTC)
        old_arrival = await store.insert_arrival_candidate(
            context_snapshot=ArrivalContextSnapshot(
                device_id=device_id,
                computed_at=cleanup_base - timedelta(days=8),
                local_time="12:00",
            ),
            behavior="wait_silent",
            computed_at=cleanup_base - timedelta(days=8),
            valid_until=cleanup_base - timedelta(seconds=1),
        )
        recent_arrival = await store.insert_arrival_candidate(
            context_snapshot=ArrivalContextSnapshot(
                device_id=device_id,
                computed_at=cleanup_base,
                local_time="12:00",
            ),
            behavior="wait_silent",
            computed_at=cleanup_base,
            valid_until=cleanup_base + timedelta(seconds=1),
        )
        arrival_ids.extend([old_arrival.id, recent_arrival.id])

        deleted_count = await store.delete_expired_arrival_candidates(
            older_than=cleanup_base
        )
        assert deleted_count >= 1
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id
                    FROM arrival_candidates
                    WHERE id = ANY(%s)
                    ORDER BY valid_until
                    """,
                    ([old_arrival.id, recent_arrival.id],),
                )
                remaining_arrivals = [row[0] for row in await cur.fetchall()]
        assert remaining_arrivals == [recent_arrival.id]
    finally:
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                if inserted_ids:
                    await cur.execute(
                        "DELETE FROM utterance_candidates WHERE id = ANY(%s)",
                        (inserted_ids,),
                    )
                if arrival_ids:
                    await cur.execute(
                        "DELETE FROM arrival_candidates WHERE id = ANY(%s)",
                        (arrival_ids,),
                    )
            await conn.commit()
