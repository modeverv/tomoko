from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import psycopg
import pytest

from server.shared.config import NodeConfig
from server.shared.models import UserContextSnapshot
from server.shared.perception import PostgresUserContextSnapshotStore


@pytest.mark.integration
async def test_postgres_user_context_snapshot_round_trip() -> None:
    config = NodeConfig.load("config/central_realtime.toml")
    dsn = config.database.dsn
    computed_at = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    device_id = "integration-t26"

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                open(
                    "docker/postgres/init/023_user_context_snapshots.sql",
                    encoding="utf-8",
                ).read()
            )
            await cur.execute(
                "DELETE FROM user_context_snapshots WHERE device_id = %s",
                (device_id,),
            )
        await conn.commit()

    store = PostgresUserContextSnapshotStore(dsn)

    try:
        snapshot = await store.insert_snapshot(
            UserContextSnapshot(
                computed_at=computed_at,
                device_id=device_id,
                present=True,
                presence_observed_at=computed_at,
                activity_label="typing",
                activity_observed_at=computed_at,
                screen_activity_label="debugging tests",
                screen_observed_at=computed_at,
                calendar_summary="10:30 設計レビュー",
                world_summary="MLX update: summary",
                user_activity_summary="present; activity=typing; screen=debugging tests",
                context_summary="user=present; readiness=needs_help_maybe",
                interaction_readiness="needs_help_maybe",
                confidence=0.7,
                source_frame_ids=(
                    UUID("00000000-0000-0000-0000-000000000001"),
                ),
                source_observation_ids=(
                    UUID("00000000-0000-0000-0000-000000000002"),
                ),
                model="deterministic-v1",
                raw_reason_json={"source_counts": {"presence": 1}},
            )
        )
        latest = await store.fetch_latest(limit=1, device_id=device_id)

        assert snapshot.id is not None
        assert latest == [snapshot]
        assert latest[0].source_frame_ids == (
            UUID("00000000-0000-0000-0000-000000000001"),
        )
        assert latest[0].raw_reason_json == {"source_counts": {"presence": 1}}
    finally:
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM user_context_snapshots WHERE device_id = %s",
                    (device_id,),
                )
            await conn.commit()
