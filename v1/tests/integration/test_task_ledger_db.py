from __future__ import annotations

from datetime import UTC, datetime

import psycopg
import pytest

from server.gateway.context import ContextSnapshotBuilder
from server.shared.config import NodeConfig
from server.shared.models import ContextBuildPolicy
from server.shared.task_ledger import PostgresTaskLedgerStore


@pytest.mark.integration
async def test_postgres_task_ledger_store_feeds_context_snapshot() -> None:
    config = NodeConfig.load("config/central_realtime.toml")
    dsn = config.database.dsn
    ddl = "docker/postgres/init/016_task_ledger.sql"
    task_ids = (
        "integration-task-low",
        "integration-task-high",
        "integration-task-done",
    )

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(open(ddl, encoding="utf-8").read())
            await cur.execute(
                "DELETE FROM task_ledger_entries WHERE id = ANY(%s)",
                (list(task_ids),),
            )
        await conn.commit()

    store = PostgresTaskLedgerStore(dsn)
    try:
        await store.upsert(
            task_id=task_ids[0],
            title="低優先の残タスク",
            priority=10,
            source="integration",
            tags=("low",),
        )
        await store.upsert(
            task_id=task_ids[1],
            title="高優先の残タスク",
            priority=90,
            due_at=datetime(2026, 6, 3, 9, 0, tzinfo=UTC),
            source="integration",
            details="ContextSnapshotBuilder に復帰する。",
            tags=("high", "context"),
        )
        await store.upsert(
            task_id=task_ids[2],
            title="完了済みタスク",
            status="completed",
            priority=100,
            source="integration",
        )

        tasks = await store.read_active_tasks(limit=10)

        assert [task.task_id for task in tasks[:2]] == [
            task_ids[1],
            task_ids[0],
        ]
        assert all(task.status == "active" for task in tasks)
        assert tasks[0].details == "ContextSnapshotBuilder に復帰する。"
        assert tasks[0].tags == ("high", "context")

        assert await store.complete_task(task_id=task_ids[1]) is True
        assert await store.complete_task(task_id=task_ids[1]) is False
        tasks_after_complete = await store.read_active_tasks(limit=10)
        assert task_ids[1] not in [task.task_id for task in tasks_after_complete]

        snapshot = await ContextSnapshotBuilder(task_ledger_store=store).build(
            text="今残っているタスクは？",
            speaker="human",
            device_id="integration-test",
            active_session_id=None,
            policy=ContextBuildPolicy.for_depth("fast"),
        )

        assert [task.task_id for task in snapshot.task_ledger_entries[:1]] == [
            task_ids[0],
        ]
        assert snapshot.trace.included_counts["task_ledger"] >= 1
        assert "task_ledger" in snapshot.trace.stage_timings_ms
    finally:
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM task_ledger_entries WHERE id = ANY(%s)",
                    (list(task_ids),),
                )
            await conn.commit()
