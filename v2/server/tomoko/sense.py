from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from server.tomoko.db_bridge import SqlCommand

SENSE_KIND_SCREENSHOT = "screenshot"
SENSE_KIND_CAMERA_PRESENCE = "camera_presence"
SENSE_KIND_WORLD_SEARCH = "world_search"
SENSE_KIND_CALENDAR_LOOKUP = "calendar_lookup"

SENSE_STATUS_PENDING = "pending"
SENSE_STATUS_CLAIMED = "claimed"
SENSE_STATUS_DONE = "done"
SENSE_STATUS_FAILED = "failed"


@dataclass(slots=True)
class SenseRequestRecord:
    kind: str
    query: str = ""
    status: str = SENSE_STATUS_PENDING
    result: dict[str, Any] = field(default_factory=dict)
    requested_by: str = ""
    requested_at: datetime | None = None
    completed_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)
    trace_id: UUID = field(default_factory=uuid4)


def insert_sense_request_sql(record: SenseRequestRecord) -> SqlCommand:
    return SqlCommand(
        """
        INSERT INTO v2_sense_requests (id, kind, query, status, requested_by, trace_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        RETURNING id
        """,
        (
            record.id,
            record.kind,
            record.query,
            record.status,
            record.requested_by,
            record.trace_id,
        ),
    )


def claim_sense_requests_sql(*, kind: str, limit: int = 4) -> SqlCommand:
    return SqlCommand(
        """
        UPDATE v2_sense_requests
        SET status = 'claimed', claimed_at = now()
        WHERE id IN (
            SELECT id
            FROM v2_sense_requests
            WHERE status = 'pending' AND kind = %s
            ORDER BY requested_at
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        )
        RETURNING id, kind, query, status, result, requested_by, requested_at,
                  completed_at, trace_id
        """,
        (kind, limit),
    )


def complete_sense_request_sql(
    request_id: UUID,
    *,
    result: dict[str, Any],
    status: str = SENSE_STATUS_DONE,
) -> SqlCommand:
    return SqlCommand(
        """
        UPDATE v2_sense_requests
        SET status = %s, result = %s::jsonb, completed_at = now()
        WHERE id = %s
        RETURNING id
        """,
        (status, json.dumps(result, ensure_ascii=False), request_id),
    )


def select_sense_request_sql(request_id: UUID) -> SqlCommand:
    return SqlCommand(
        """
        SELECT id, kind, query, status, result, requested_by, requested_at,
               completed_at, trace_id
        FROM v2_sense_requests
        WHERE id = %s
        """,
        (request_id,),
    )


def sense_request_from_row(row: dict[str, Any]) -> SenseRequestRecord:
    result = row.get("result") or {}
    if isinstance(result, str):
        result = json.loads(result)
    return SenseRequestRecord(
        id=row["id"],
        kind=str(row["kind"]),
        query=str(row.get("query") or ""),
        status=str(row["status"]),
        result=dict(result),
        requested_by=str(row.get("requested_by") or ""),
        requested_at=row.get("requested_at"),
        completed_at=row.get("completed_at"),
        trace_id=row["trace_id"],
    )


async def claim_pending_sense_requests(
    conn: Any,
    *,
    kind: str,
    limit: int = 4,
) -> list[SenseRequestRecord]:
    command = claim_sense_requests_sql(kind=kind, limit=limit)
    cursor = await conn.execute(command.query, command.params)
    rows = await cursor.fetchall()
    return [sense_request_from_row(row) for row in rows]


async def load_sense_request(conn: Any, request_id: UUID) -> SenseRequestRecord | None:
    command = select_sense_request_sql(request_id)
    cursor = await conn.execute(command.query, command.params)
    row = await cursor.fetchone()
    if row is None:
        return None
    return sense_request_from_row(row)


def insert_user_status_observation_sql(observation: Any) -> SqlCommand:
    return SqlCommand(
        """
        INSERT INTO v2_user_status_observations (
            id, present, activity_label, summary, confidence, visible_text,
            app_name, window_title, url, artifact_path, source, trace_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        RETURNING id
        """,
        (
            observation.id,
            observation.present,
            observation.activity_label,
            observation.summary,
            observation.confidence,
            observation.visible_text,
            observation.app_name,
            observation.window_title,
            observation.url,
            observation.artifact_path,
            observation.source,
            observation.trace_id,
        ),
    )


def create_fake_sense_executor_from_env(env_key: str = "TOMOKO_V2_FAKE_SENSE"):
    """env に JSON があれば、それを即返す fake executor を作る(fake runtime 用)。"""
    import os

    raw = os.environ.get(env_key)
    if not raw:
        return None
    payload = json.loads(raw)

    async def fake_executor(record: SenseRequestRecord) -> dict[str, Any]:
        del record
        return dict(payload)

    return fake_executor
