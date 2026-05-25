from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Protocol
from uuid import UUID, uuid4

import psycopg


@dataclass(frozen=True)
class DiaryEntry:
    id: UUID
    diary_date: date
    body_text: str
    diary_version: int = 1
    source_session_ids: tuple[UUID, ...] = field(default_factory=tuple)
    source_candidate_ids: tuple[UUID, ...] = field(default_factory=tuple)
    source_world_observation_interpretation_ids: tuple[UUID, ...] = field(
        default_factory=tuple
    )
    mood: str | None = None
    schema_version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.body_text.strip():
            raise ValueError("DiaryEntry.body_text must not be empty")
        if self.diary_version < 1:
            raise ValueError("DiaryEntry.diary_version must be positive")
        if self.schema_version != 1:
            raise ValueError(f"Unsupported diary schema_version: {self.schema_version}")

    @classmethod
    def from_db_row(cls, row: tuple[object, ...]) -> DiaryEntry:
        (
            entry_id,
            diary_date,
            body_text,
            diary_version,
            source_session_ids,
            source_candidate_ids,
            source_world_observation_interpretation_ids,
            mood,
            schema_version,
            created_at,
        ) = row
        return cls(
            id=_as_uuid(entry_id),
            diary_date=_as_date(diary_date),
            body_text=str(body_text),
            diary_version=int(diary_version),
            source_session_ids=tuple(_as_uuid(item) for item in source_session_ids or ()),
            source_candidate_ids=tuple(
                _as_uuid(item) for item in source_candidate_ids or ()
            ),
            source_world_observation_interpretation_ids=tuple(
                _as_uuid(item)
                for item in source_world_observation_interpretation_ids or ()
            ),
            mood=_optional_str(mood),
            schema_version=int(schema_version),
            created_at=_as_datetime(created_at),
        )


class DiaryStore(Protocol):
    async def insert_entry(
        self,
        *,
        diary_date: date,
        body_text: str,
        source_session_ids: tuple[UUID, ...] = (),
        source_candidate_ids: tuple[UUID, ...] = (),
        source_world_observation_interpretation_ids: tuple[UUID, ...] = (),
        mood: str | None = None,
        created_at: datetime | None = None,
    ) -> DiaryEntry: ...

    async def fetch_recent_entries(self, *, limit: int) -> list[DiaryEntry]: ...


class InMemoryDiaryStore:
    def __init__(self) -> None:
        self.entries: list[DiaryEntry] = []

    async def insert_entry(
        self,
        *,
        diary_date: date,
        body_text: str,
        source_session_ids: tuple[UUID, ...] = (),
        source_candidate_ids: tuple[UUID, ...] = (),
        source_world_observation_interpretation_ids: tuple[UUID, ...] = (),
        mood: str | None = None,
        created_at: datetime | None = None,
    ) -> DiaryEntry:
        entry = DiaryEntry(
            id=uuid4(),
            diary_date=diary_date,
            body_text=body_text,
            diary_version=1
            + sum(1 for entry in self.entries if entry.diary_date == diary_date),
            source_session_ids=source_session_ids,
            source_candidate_ids=source_candidate_ids,
            source_world_observation_interpretation_ids=(
                source_world_observation_interpretation_ids
            ),
            mood=mood,
            created_at=created_at or datetime.now(UTC),
        )
        self.entries.append(entry)
        return entry

    async def fetch_recent_entries(self, *, limit: int) -> list[DiaryEntry]:
        return sorted(
            self.entries,
            key=lambda entry: (entry.diary_date, entry.created_at),
            reverse=True,
        )[:limit]


class PostgresDiaryStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def insert_entry(
        self,
        *,
        diary_date: date,
        body_text: str,
        source_session_ids: tuple[UUID, ...] = (),
        source_candidate_ids: tuple[UUID, ...] = (),
        source_world_observation_interpretation_ids: tuple[UUID, ...] = (),
        mood: str | None = None,
        created_at: datetime | None = None,
    ) -> DiaryEntry:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    WITH next_version AS (
                        SELECT COALESCE(MAX(diary_version), 0) + 1 AS value
                        FROM diary_entries
                        WHERE diary_date = %s
                    )
                    INSERT INTO diary_entries (
                        diary_date,
                        body_text,
                        diary_version,
                        source_session_ids,
                        source_candidate_ids,
                        source_world_observation_interpretation_ids,
                        mood,
                        created_at
                    )
                    SELECT
                        %s,
                        %s,
                        next_version.value,
                        %s,
                        %s,
                        %s,
                        %s,
                        COALESCE(%s, now())
                    FROM next_version
                    RETURNING
                        id,
                        diary_date,
                        body_text,
                        diary_version,
                        source_session_ids,
                        source_candidate_ids,
                        source_world_observation_interpretation_ids,
                        mood,
                        schema_version,
                        created_at
                    """,
                    (
                        diary_date,
                        diary_date,
                        body_text,
                        list(source_session_ids),
                        list(source_candidate_ids),
                        list(source_world_observation_interpretation_ids),
                        mood,
                        created_at,
                    ),
                )
                row = await cur.fetchone()
        if row is None:
            raise RuntimeError("diary entry insert returned no row")
        return DiaryEntry.from_db_row(row)

    async def fetch_recent_entries(self, *, limit: int) -> list[DiaryEntry]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        id,
                        diary_date,
                        body_text,
                        diary_version,
                        source_session_ids,
                        source_candidate_ids,
                        source_world_observation_interpretation_ids,
                        mood,
                        schema_version,
                        created_at
                    FROM diary_entries
                    ORDER BY diary_date DESC, created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = await cur.fetchall()
        return [DiaryEntry.from_db_row(row) for row in rows]


def _as_uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _as_date(value: object) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError(f"Expected date value, got {type(value)!r}")


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
