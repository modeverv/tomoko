from __future__ import annotations

from datetime import UTC, datetime

import psycopg
import pytest

from server.shared.config import NodeConfig
from server.shared.perception import (
    PostgresHumanPresenceObservationStore,
    PostgresPerceptionFrameStore,
)


@pytest.mark.integration
async def test_postgres_human_presence_observation_round_trip() -> None:
    config = NodeConfig.load("config/central_realtime.toml")
    dsn = config.database.dsn
    path_prefix = "integration-presence/t23"
    observed_at = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                open(
                    "docker/postgres/init/019_perception_frames.sql",
                    encoding="utf-8",
                ).read()
            )
            await cur.execute(
                open(
                    "docker/postgres/init/020_human_presence_observations.sql",
                    encoding="utf-8",
                ).read()
            )
            await cur.execute(
                "DELETE FROM perception_frames WHERE file_path LIKE %s",
                (f"{path_prefix}%",),
            )
        await conn.commit()

    frame_store = PostgresPerceptionFrameStore(dsn)
    observation_store = PostgresHumanPresenceObservationStore(dsn)

    try:
        frame = await frame_store.insert_frame(
            source="camera",
            file_path=f"{path_prefix}/frame.jpg",
            sha256="integration-presence-frame",
            captured_at=observed_at,
        )
        assert frame.id is not None

        observation = await observation_store.insert_observation(
            frame_id=frame.id,
            observed_at=observed_at,
            present=True,
            confidence=0.77,
            model="integration-presence",
            raw_reason_json={"reason": "person visible"},
        )
        fetched = await observation_store.fetch_by_frame(frame.id)
        latest = await observation_store.fetch_latest(limit=5)

        assert observation.id is not None
        assert fetched == observation
        assert latest[0] == observation
        assert fetched.raw_reason_json == {"reason": "person visible"}  # type: ignore[union-attr]
    finally:
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM perception_frames WHERE file_path LIKE %s",
                    (f"{path_prefix}%",),
                )
            await conn.commit()
