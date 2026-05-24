from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from server.shared.candidate import ArrivalContextSnapshot, PostgresCandidateStore
from server.shared.config import NodeConfig


@pytest.mark.integration
async def test_postgres_candidate_store_round_trip() -> None:
    config = NodeConfig.load("config/central_realtime.toml")
    dsn = config.database.dsn
    ddl = "docker/postgres/init/006_candidates.sql"

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(open(ddl, encoding="utf-8").read())
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
