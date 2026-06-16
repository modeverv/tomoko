from __future__ import annotations

from datetime import UTC, datetime

import psycopg
import pytest

from server.shared.config import NodeConfig
from server.shared.perception import (
    PostgresPerceptionFrameStore,
    PostgresScreenActivityObservationStore,
)


@pytest.mark.integration
async def test_postgres_screen_activity_observation_round_trip() -> None:
    config = NodeConfig.load("config/central_realtime.toml")
    dsn = config.database.dsn
    path_prefix = "integration-screen/t25"
    observed_at = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            for ddl in (
                "docker/postgres/init/019_perception_frames.sql",
                "docker/postgres/init/022_screen_activity_observations.sql",
            ):
                await cur.execute(open(ddl, encoding="utf-8").read())
            await cur.execute(
                "DELETE FROM perception_frames WHERE file_path LIKE %s",
                (f"{path_prefix}%",),
            )
        await conn.commit()

    frame_store = PostgresPerceptionFrameStore(dsn)
    screen_store = PostgresScreenActivityObservationStore(dsn)

    try:
        frame = await frame_store.insert_frame(
            source="screenshot",
            file_path=f"{path_prefix}/frame.png",
            sha256="integration-screen-frame",
            captured_at=observed_at,
        )
        assert frame.id is not None

        observation = await screen_store.insert_observation(
            frame_id=frame.id,
            observed_at=observed_at,
            screen_activity_label="debugging tests",
            app_hint="Terminal",
            document_hint="pytest",
            url_hint=None,
            confidence=0.79,
            model="integration-screen",
            raw_reason_json={"reason": "pytest output visible"},
        )
        fetched = await screen_store.fetch_by_frame(frame.id)
        latest = await screen_store.fetch_latest(limit=5)

        assert observation.id is not None
        assert fetched == observation
        assert latest[0] == observation
        assert fetched.app_hint == "Terminal"  # type: ignore[union-attr]
        assert fetched.document_hint == "pytest"  # type: ignore[union-attr]
    finally:
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM perception_frames WHERE file_path LIKE %s",
                    (f"{path_prefix}%",),
                )
            await conn.commit()
