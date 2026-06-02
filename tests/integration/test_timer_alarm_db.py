from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from server.shared.config import NodeConfig
from server.shared.timer_alarm import PostgresTimerAlarmStore


@pytest.mark.integration
async def test_postgres_timer_alarm_store_create_claim_notify() -> None:
    config = NodeConfig.load("config/central_realtime.toml")
    dsn = config.database.dsn
    ddl = "docker/postgres/init/017_timer_alarm.sql"
    entry_ids = (
        "integration-timer-past",
        "integration-timer-future",
        "integration-alarm-001",
    )

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(open(ddl, encoding="utf-8").read())
            await cur.execute(
                "DELETE FROM timer_alarm_entries WHERE id = ANY(%s)",
                (list(entry_ids),),
            )
        await conn.commit()

    store = PostgresTimerAlarmStore(dsn)
    now = datetime.now(UTC)

    try:
        await store.create(
            entry_id=entry_ids[0],
            kind="timer",
            label="過去タイマー",
            due_at=now - timedelta(seconds=10),
        )
        await store.create(
            entry_id=entry_ids[1],
            kind="timer",
            label="未来タイマー",
            due_at=now + timedelta(hours=1),
        )
        await store.create(
            entry_id=entry_ids[2],
            kind="alarm",
            label="明日9時アラーム",
            due_at=now + timedelta(days=1),
        )

        claimed = await store.claim_due(worker_id="test-worker", now=now, limit=10)

        assert len(claimed) == 1
        assert claimed[0].entry_id == entry_ids[0]
        assert claimed[0].status == "due"
        assert claimed[0].kind == "timer"

        claimed_again = await store.claim_due(worker_id="test-worker", now=now, limit=10)
        assert claimed_again == []

        notified = await store.mark_notified(entry_id=entry_ids[0])
        assert notified is True

        notified_again = await store.mark_notified(entry_id=entry_ids[0])
        assert notified_again is False

        cancelled = await store.cancel(entry_id=entry_ids[1])
        assert cancelled is True

        cancelled_again = await store.cancel(entry_id=entry_ids[1])
        assert cancelled_again is False

    finally:
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM timer_alarm_entries WHERE id = ANY(%s)",
                    (list(entry_ids),),
                )
            await conn.commit()


@pytest.mark.integration
async def test_postgres_timer_alarm_store_mark_failed() -> None:
    config = NodeConfig.load("config/central_realtime.toml")
    dsn = config.database.dsn
    ddl = "docker/postgres/init/017_timer_alarm.sql"
    entry_id = "integration-timer-fail-001"

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(open(ddl, encoding="utf-8").read())
            await cur.execute(
                "DELETE FROM timer_alarm_entries WHERE id = %s",
                (entry_id,),
            )
        await conn.commit()

    store = PostgresTimerAlarmStore(dsn)
    now = datetime.now(UTC)

    try:
        await store.create(
            entry_id=entry_id,
            kind="timer",
            label="失敗テスト",
            due_at=now - timedelta(seconds=5),
        )
        await store.claim_due(worker_id="test-worker", now=now, limit=10)

        result = await store.mark_failed(entry_id=entry_id, reason="audio_target_unavailable")
        assert result is True

        result_again = await store.mark_failed(entry_id=entry_id, reason="retry")
        assert result_again is False

    finally:
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM timer_alarm_entries WHERE id = %s",
                    (entry_id,),
                )
            await conn.commit()
