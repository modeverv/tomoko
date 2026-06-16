from __future__ import annotations

from datetime import UTC, datetime

import psycopg
import pytest

from server.shared.config import NodeConfig
from server.shared.perception import (
    PostgresHumanActivityObservationStore,
    PostgresHumanPresenceObservationStore,
    PostgresPerceptionFrameStore,
)


@pytest.mark.integration
async def test_postgres_human_activity_observation_round_trip() -> None:
    config = NodeConfig.load("config/central_realtime.toml")
    dsn = config.database.dsn
    path_prefix = "integration-activity/t24"
    observed_at = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            for ddl in (
                "docker/postgres/init/019_perception_frames.sql",
                "docker/postgres/init/020_human_presence_observations.sql",
                "docker/postgres/init/021_human_activity_observations.sql",
            ):
                await cur.execute(open(ddl, encoding="utf-8").read())
            await cur.execute(
                "DELETE FROM perception_frames WHERE file_path LIKE %s",
                (f"{path_prefix}%",),
            )
        await conn.commit()

    frame_store = PostgresPerceptionFrameStore(dsn)
    presence_store = PostgresHumanPresenceObservationStore(dsn)
    activity_store = PostgresHumanActivityObservationStore(dsn)

    try:
        frame = await frame_store.insert_frame(
            source="camera",
            file_path=f"{path_prefix}/frame.jpg",
            sha256="integration-activity-frame",
            captured_at=observed_at,
        )
        assert frame.id is not None
        presence = await presence_store.insert_observation(
            frame_id=frame.id,
            observed_at=observed_at,
            present=True,
            confidence=0.8,
            model="integration-presence",
        )

        observation = await activity_store.insert_observation(
            frame_id=frame.id,
            presence_observation_id=presence.id,
            observed_at=observed_at,
            activity_label="typing",
            confidence=0.77,
            model="integration-activity",
            raw_reason_json={"reason": "keyboard visible"},
        )
        fetched = await activity_store.fetch_by_frame(frame.id)
        latest = await activity_store.fetch_latest(limit=5)

        assert observation.id is not None
        assert fetched == observation
        assert latest[0] == observation
        assert fetched.presence_observation_id == presence.id  # type: ignore[union-attr]
        assert fetched.raw_reason_json == {"reason": "keyboard visible"}  # type: ignore[union-attr]
    finally:
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM perception_frames WHERE file_path LIKE %s",
                    (f"{path_prefix}%",),
                )
            await conn.commit()
