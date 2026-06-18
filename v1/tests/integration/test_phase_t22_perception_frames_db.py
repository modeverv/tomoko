from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from server.shared.config import NodeConfig
from server.shared.perception import PostgresPerceptionFrameStore


@pytest.mark.integration
async def test_postgres_perception_frame_store_round_trip_and_retention() -> None:
    config = NodeConfig.load("config/central_realtime.toml")
    dsn = config.database.dsn
    ddl = "docker/postgres/init/019_perception_frames.sql"
    start = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    path_prefix = "integration-perception/t22"

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(open(ddl, encoding="utf-8").read())
            await cur.execute(
                "DELETE FROM perception_frames WHERE file_path LIKE %s",
                (f"{path_prefix}%",),
            )
        await conn.commit()

    store = PostgresPerceptionFrameStore(dsn)

    try:
        old = await store.insert_frame(
            source="camera",
            file_path=f"{path_prefix}/old.jpg",
            sha256="integration-old",
            captured_at=start,
            device_id="desk",
            width=640,
            height=480,
        )
        await store.insert_frame(
            source="camera",
            file_path=f"{path_prefix}/new.jpg",
            sha256="integration-new",
            captured_at=start + timedelta(seconds=1),
            device_id="desk",
        )

        retired_count = await store.apply_retention(source="camera", keep_latest=1)
        retained = await store.fetch_retained_frames(source="camera", limit=10)
        old_after = await store.fetch_frame(old.id)

        assert retired_count >= 1
        assert any(frame.file_path == f"{path_prefix}/new.jpg" for frame in retained)
        assert old_after is not None
        assert old_after.retained is False
        assert old_after.width == 640
        assert old_after.height == 480
    finally:
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM perception_frames WHERE file_path LIKE %s",
                    (f"{path_prefix}%",),
                )
            await conn.commit()
