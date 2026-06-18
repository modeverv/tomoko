from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID, uuid4

import psycopg

EdgeStatusValue = Literal["online", "degraded", "offline"]


@dataclass(frozen=True)
class PresenceReport:
    id: UUID
    device_id: str
    observed_at: datetime
    audio_level_db: float
    transcript_id: UUID | None = None
    transcript_text: str | None = None
    is_speaking: bool = True

    def __post_init__(self) -> None:
        if not self.device_id:
            raise ValueError("PresenceReport.device_id must not be empty")

    @classmethod
    def from_db_row(cls, row: tuple[object, ...]) -> PresenceReport:
        (
            report_id,
            device_id,
            observed_at,
            audio_level_db,
            transcript_id,
            transcript_text,
            is_speaking,
        ) = row
        return cls(
            id=_as_uuid(report_id),
            device_id=str(device_id),
            observed_at=_as_datetime(observed_at),
            audio_level_db=float(audio_level_db),
            transcript_id=_optional_uuid(transcript_id),
            transcript_text=_optional_str(transcript_text),
            is_speaking=bool(is_speaking),
        )


@dataclass(frozen=True)
class EdgeStatus:
    device_id: str
    status: EdgeStatusValue
    last_seen_at: datetime
    role: str = "edge"
    detail: str | None = None

    def __post_init__(self) -> None:
        if not self.device_id:
            raise ValueError("EdgeStatus.device_id must not be empty")
        if self.status not in {"online", "degraded", "offline"}:
            raise ValueError(f"Unsupported edge status: {self.status}")

    @classmethod
    def from_db_row(cls, row: tuple[object, ...]) -> EdgeStatus:
        device_id, status, last_seen_at, role, detail = row
        return cls(
            device_id=str(device_id),
            status=_as_status(status),
            last_seen_at=_as_datetime(last_seen_at),
            role=str(role),
            detail=_optional_str(detail),
        )


class PresenceStore(Protocol):
    async def insert_presence_report(
        self,
        *,
        device_id: str,
        audio_level_db: float,
        observed_at: datetime,
        transcript_id: UUID | None = None,
        transcript_text: str | None = None,
        is_speaking: bool = True,
    ) -> PresenceReport: ...

    async def fetch_recent_presence_reports(
        self,
        *,
        since: datetime,
        limit: int,
    ) -> tuple[PresenceReport, ...]: ...

    async def upsert_edge_status(
        self,
        *,
        device_id: str,
        status: EdgeStatusValue,
        last_seen_at: datetime,
        role: str = "edge",
        detail: str | None = None,
    ) -> EdgeStatus: ...


class InMemoryPresenceStore:
    def __init__(self) -> None:
        self.reports: list[PresenceReport] = []
        self.statuses: dict[str, EdgeStatus] = {}

    async def insert_presence_report(
        self,
        *,
        device_id: str,
        audio_level_db: float,
        observed_at: datetime,
        transcript_id: UUID | None = None,
        transcript_text: str | None = None,
        is_speaking: bool = True,
    ) -> PresenceReport:
        report = PresenceReport(
            id=uuid4(),
            device_id=device_id,
            observed_at=observed_at,
            audio_level_db=audio_level_db,
            transcript_id=transcript_id,
            transcript_text=transcript_text,
            is_speaking=is_speaking,
        )
        self.reports.append(report)
        return report

    async def fetch_recent_presence_reports(
        self,
        *,
        since: datetime,
        limit: int,
    ) -> tuple[PresenceReport, ...]:
        return tuple(
            sorted(
                (report for report in self.reports if report.observed_at >= since),
                key=lambda report: report.observed_at,
                reverse=True,
            )[:limit]
        )

    async def upsert_edge_status(
        self,
        *,
        device_id: str,
        status: EdgeStatusValue,
        last_seen_at: datetime,
        role: str = "edge",
        detail: str | None = None,
    ) -> EdgeStatus:
        edge_status = EdgeStatus(
            device_id=device_id,
            status=status,
            last_seen_at=last_seen_at,
            role=role,
            detail=detail,
        )
        self.statuses[device_id] = edge_status
        return edge_status


class PostgresPresenceStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def insert_presence_report(
        self,
        *,
        device_id: str,
        audio_level_db: float,
        observed_at: datetime,
        transcript_id: UUID | None = None,
        transcript_text: str | None = None,
        is_speaking: bool = True,
    ) -> PresenceReport:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO presence_reports (
                        device_id,
                        observed_at,
                        audio_level_db,
                        transcript_id,
                        transcript_text,
                        is_speaking
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING
                        id,
                        device_id,
                        observed_at,
                        audio_level_db,
                        transcript_id,
                        transcript_text,
                        is_speaking
                    """,
                    (
                        device_id,
                        observed_at,
                        audio_level_db,
                        transcript_id,
                        transcript_text,
                        is_speaking,
                    ),
                )
                row = await cur.fetchone()
        if row is None:
            raise RuntimeError("presence report insert returned no row")
        return PresenceReport.from_db_row(row)

    async def fetch_recent_presence_reports(
        self,
        *,
        since: datetime,
        limit: int,
    ) -> tuple[PresenceReport, ...]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        id,
                        device_id,
                        observed_at,
                        audio_level_db,
                        transcript_id,
                        transcript_text,
                        is_speaking
                    FROM presence_reports
                    WHERE observed_at >= %s
                    ORDER BY observed_at DESC
                    LIMIT %s
                    """,
                    (since, limit),
                )
                rows = await cur.fetchall()
        return tuple(PresenceReport.from_db_row(row) for row in rows)

    async def upsert_edge_status(
        self,
        *,
        device_id: str,
        status: EdgeStatusValue,
        last_seen_at: datetime,
        role: str = "edge",
        detail: str | None = None,
    ) -> EdgeStatus:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO edge_status (
                        device_id,
                        status,
                        last_seen_at,
                        role,
                        detail
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (device_id) DO UPDATE
                    SET status = EXCLUDED.status,
                        last_seen_at = EXCLUDED.last_seen_at,
                        role = EXCLUDED.role,
                        detail = EXCLUDED.detail
                    RETURNING device_id, status, last_seen_at, role, detail
                    """,
                    (device_id, status, last_seen_at, role, detail),
                )
                row = await cur.fetchone()
        if row is None:
            raise RuntimeError("edge status upsert returned no row")
        return EdgeStatus.from_db_row(row)


def _as_uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _optional_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    return _as_uuid(value)


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"Expected datetime value, got {type(value)!r}")


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _as_status(value: object) -> EdgeStatusValue:
    text = str(value)
    if text not in {"online", "degraded", "offline"}:
        raise ValueError(f"Unsupported edge status: {text}")
    return text  # type: ignore[return-value]
