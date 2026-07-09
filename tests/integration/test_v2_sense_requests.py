from __future__ import annotations

import asyncio
import os
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from server.info.worker import process_world_search_sense_requests
from server.tomoko.sense import (
    SENSE_KIND_CAMERA_PRESENCE,
    SENSE_KIND_SCREENSHOT,
    SENSE_KIND_WORLD_SEARCH,
    SENSE_STATUS_DONE,
    SenseRequestRecord,
    claim_pending_sense_requests,
    complete_sense_request_sql,
    insert_sense_request_sql,
    load_sense_request,
)
from server.user_status.main import OSMetadata
from server.user_status.ocr_runtime import OcrRuntimeResult
from server.user_status.worker import (
    process_camera_presence_sense_requests,
    process_screenshot_sense_requests,
)

pytestmark = pytest.mark.integration


def test_v2_sense_request_insert_claim_complete_roundtrip() -> None:
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is required for v2 DB integration test")

    async def scenario() -> None:
        core_ddl = Path("docker/postgres/init/100_v2_core.sql").read_text(encoding="utf-8")
        sense_ddl = Path("docker/postgres/init/101_v2_sense.sql").read_text(encoding="utf-8")
        async with await psycopg.AsyncConnection.connect(
            dsn,
            autocommit=True,
            row_factory=dict_row,
        ) as conn:
            await conn.execute(core_ddl)
            await conn.execute(sense_ddl)
            record = SenseRequestRecord(
                kind=SENSE_KIND_SCREENSHOT,
                query="画面の様子",
                requested_by="test",
            )
            command = insert_sense_request_sql(record)
            await conn.execute(command.query, command.params)

            claimed = await claim_pending_sense_requests(
                conn,
                kind=SENSE_KIND_SCREENSHOT,
                limit=8,
            )
            claimed_ids = {item.id for item in claimed}
            assert record.id in claimed_ids
            assert all(item.status == "claimed" for item in claimed)

            again = await claim_pending_sense_requests(conn, kind=SENSE_KIND_SCREENSHOT)
            assert record.id not in {item.id for item in again}

            complete = complete_sense_request_sql(
                record.id,
                result={"summary": "エディタでコードを見ている", "activity": "coding_or_terminal"},
            )
            await conn.execute(complete.query, complete.params)

            loaded = await load_sense_request(conn, record.id)
            assert loaded is not None
            assert loaded.status == SENSE_STATUS_DONE
            assert loaded.result["activity"] == "coding_or_terminal"
            assert loaded.completed_at is not None

    asyncio.run(scenario())


def test_v2_user_status_worker_consumes_screenshot_sense_request() -> None:
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is required for v2 DB integration test")

    def fake_capture() -> OcrRuntimeResult:
        return OcrRuntimeResult(
            screenshot_path=Path("logs/user-status/fake.png"),
            text="def handle_observation(...): エディタでコード編集中",
            metadata=OSMetadata(app_name="Emacs", window_title="conversation.py"),
            activity_label="coding_or_terminal",
            present=True,
        )

    async def scenario() -> None:
        core_ddl = Path("docker/postgres/init/100_v2_core.sql").read_text(encoding="utf-8")
        sense_ddl = Path("docker/postgres/init/101_v2_sense.sql").read_text(encoding="utf-8")
        async with await psycopg.AsyncConnection.connect(
            dsn,
            autocommit=True,
            row_factory=dict_row,
        ) as conn:
            await conn.execute(core_ddl)
            await conn.execute(sense_ddl)
            record = SenseRequestRecord(
                kind=SENSE_KIND_SCREENSHOT,
                query="今の画面何してる?",
                requested_by="test-worker",
            )
            command = insert_sense_request_sql(record)
            await conn.execute(command.query, command.params)

            result = await process_screenshot_sense_requests(
                conn,
                capture=fake_capture,
                limit=8,
            )
            assert result.requests_done >= 1
            assert result.requests_failed == 0

            loaded = await load_sense_request(conn, record.id)
            assert loaded is not None
            assert loaded.status == SENSE_STATUS_DONE
            assert loaded.result["app_name"] == "Emacs"

            cursor = await conn.execute(
                "SELECT source, app_name FROM v2_user_status_observations WHERE trace_id = %s",
                (record.trace_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row["app_name"] == "Emacs"

    asyncio.run(scenario())


def test_v2_info_worker_consumes_world_search_sense_request() -> None:
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is required for v2 DB integration test")

    def fake_search(query: str) -> list[dict[str, object]]:
        assert "天気" in query
        return [
            {"source_key": "weather-1", "text": "明日は雨のち晴れ、最高28度", "confidence": 0.8},
            {"text": ""},
        ]

    async def scenario() -> None:
        core_ddl = Path("docker/postgres/init/100_v2_core.sql").read_text(encoding="utf-8")
        sense_ddl = Path("docker/postgres/init/101_v2_sense.sql").read_text(encoding="utf-8")
        async with await psycopg.AsyncConnection.connect(
            dsn,
            autocommit=True,
            row_factory=dict_row,
        ) as conn:
            await conn.execute(core_ddl)
            await conn.execute(sense_ddl)
            record = SenseRequestRecord(
                kind=SENSE_KIND_WORLD_SEARCH,
                query="明日の天気",
                requested_by="test-info-worker",
            )
            command = insert_sense_request_sql(record)
            await conn.execute(command.query, command.params)

            result = await process_world_search_sense_requests(
                conn,
                search=fake_search,
                limit=8,
            )
            assert result.requests_done >= 1
            assert result.items_upserted >= 1

            loaded = await load_sense_request(conn, record.id)
            assert loaded is not None
            assert loaded.status == SENSE_STATUS_DONE
            assert loaded.result["texts"] == ["明日は雨のち晴れ、最高28度"]

            cursor = await conn.execute(
                "SELECT summary FROM v2_world_interpretations WHERE summary LIKE %s",
                ("%明日は雨のち晴れ%",),
            )
            row = await cursor.fetchone()
            assert row is not None

    asyncio.run(scenario())


def test_v2_user_status_worker_consumes_camera_presence_sense_request() -> None:
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is required for v2 DB integration test")

    def fake_presence() -> dict[str, object]:
        return {"present": True, "faces": 1, "confidence": 0.92}

    async def scenario() -> None:
        core_ddl = Path("docker/postgres/init/100_v2_core.sql").read_text(encoding="utf-8")
        sense_ddl = Path("docker/postgres/init/101_v2_sense.sql").read_text(encoding="utf-8")
        async with await psycopg.AsyncConnection.connect(
            dsn,
            autocommit=True,
            row_factory=dict_row,
        ) as conn:
            await conn.execute(core_ddl)
            await conn.execute(sense_ddl)
            record = SenseRequestRecord(
                kind=SENSE_KIND_CAMERA_PRESENCE,
                requested_by="test-camera",
            )
            command = insert_sense_request_sql(record)
            await conn.execute(command.query, command.params)

            result = await process_camera_presence_sense_requests(
                conn,
                presence=fake_presence,
                limit=8,
            )
            assert result.requests_done >= 1

            loaded = await load_sense_request(conn, record.id)
            assert loaded is not None
            assert loaded.status == SENSE_STATUS_DONE
            assert loaded.result["present"] is True

            cursor = await conn.execute(
                "SELECT present, source FROM v2_user_status_observations WHERE trace_id = %s",
                (record.trace_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row["source"] == "camera"
            assert row["present"] is True

    asyncio.run(scenario())
