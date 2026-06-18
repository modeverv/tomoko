from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from server.shared.config import NodeConfig
from server.shared.presence import PostgresPresenceStore


@pytest.mark.integration
async def test_postgres_presence_store_round_trip_without_audio_bytes() -> None:
    config = NodeConfig.load("config/central_realtime.toml")
    dsn = config.database.dsn
    ddl = "docker/postgres/init/010_presence.sql"
    device_id = "phase14-kitchen"
    now = datetime(2099, 5, 24, 23, 30, tzinfo=UTC)
    transcript_id = uuid4()

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(open(ddl, encoding="utf-8").read())
            await cur.execute(
                "DELETE FROM presence_reports WHERE device_id = %s",
                (device_id,),
            )
            await cur.execute(
                "DELETE FROM edge_status WHERE device_id = %s",
                (device_id,),
            )
        await conn.commit()

    store = PostgresPresenceStore(dsn)
    try:
        report = await store.insert_presence_report(
            device_id=device_id,
            audio_level_db=-18.5,
            observed_at=now,
            transcript_id=transcript_id,
            transcript_text="今日いい天気",
        )
        status = await store.upsert_edge_status(
            device_id=device_id,
            status="online",
            last_seen_at=now,
            detail="unit smoke",
        )
        recent = await store.fetch_recent_presence_reports(
            since=now - timedelta(seconds=1),
            limit=10,
        )

        assert report in recent
        assert report.transcript_id == transcript_id
        assert report.transcript_text == "今日いい天気"
        assert not hasattr(report, "audio")
        assert not hasattr(report, "audio_bytes")
        assert status.status == "online"
        assert status.detail == "unit smoke"
    finally:
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM presence_reports WHERE device_id = %s",
                    (device_id,),
                )
                await cur.execute(
                    "DELETE FROM edge_status WHERE device_id = %s",
                    (device_id,),
                )
            await conn.commit()
