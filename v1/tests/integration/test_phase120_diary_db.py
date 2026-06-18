from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import psycopg
import pytest

from server.shared.config import NodeConfig
from server.shared.diary import PostgresDiaryStore


@pytest.mark.integration
async def test_postgres_diary_store_versions_same_date_entries() -> None:
    config = NodeConfig.load("config/central_realtime.toml")
    dsn = config.database.dsn
    ddl = "docker/postgres/init/007_diary.sql"
    diary_date = date(2099, 5, 24)
    session_id = uuid4()
    candidate_id = uuid4()

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(open(ddl, encoding="utf-8").read())
            await cur.execute(
                "DELETE FROM diary_entries WHERE diary_date = %s",
                (diary_date,),
            )
        await conn.commit()

    store = PostgresDiaryStore(dsn)
    try:
        first = await store.insert_entry(
            diary_date=diary_date,
            body_text="一回目の日記。",
            source_session_ids=(session_id,),
            created_at=datetime(2099, 5, 24, 22, 0, tzinfo=UTC),
        )
        second = await store.insert_entry(
            diary_date=diary_date,
            body_text="二回目の日記。",
            source_candidate_ids=(candidate_id,),
            created_at=datetime(2099, 5, 24, 23, 0, tzinfo=UTC),
        )

        recent = await store.fetch_recent_entries(limit=2)

        assert first.diary_version == 1
        assert second.diary_version == 2
        assert recent[0].body_text == "二回目の日記。"
        assert recent[0].source_candidate_ids == (candidate_id,)
        assert recent[1].source_session_ids == (session_id,)
    finally:
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM diary_entries WHERE diary_date = %s",
                    (diary_date,),
                )
            await conn.commit()
