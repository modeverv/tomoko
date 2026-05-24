from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from uuid import UUID

import psycopg


@dataclass(frozen=True)
class SessionSummaryMaterial:
    id: UUID
    started_at: datetime
    ended_at: datetime
    summary_text: str


@dataclass(frozen=True)
class ConversationTurnMaterial:
    id: UUID
    conversation_session_id: UUID | None
    role: str
    text: str
    emotion: str | None
    status: str
    recorded_at: datetime

    def __post_init__(self) -> None:
        if self.role not in {"user", "tomoko"}:
            raise ValueError(f"Unsupported conversation role: {self.role}")
        if self.status not in {"completed", "interrupted", "cancelled", "error"}:
            raise ValueError(f"Unsupported conversation status: {self.status}")


@dataclass(frozen=True)
class AmbientDigest:
    total_count: int
    excerpts: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DismissedCandidateMaterial:
    id: UUID
    seed: str
    generated_text: str | None
    priority: float
    dismissed_at: datetime


@dataclass(frozen=True)
class JournalistInputSnapshot:
    diary_date: date
    started_at: datetime
    ended_at: datetime
    session_summaries: tuple[SessionSummaryMaterial, ...]
    conversation_turns: tuple[ConversationTurnMaterial, ...]
    ambient_digest: AmbientDigest
    dismissed_candidates: tuple[DismissedCandidateMaterial, ...]

    @property
    def source_session_ids(self) -> tuple[UUID, ...]:
        ordered: list[UUID] = []
        for summary in self.session_summaries:
            if summary.id not in ordered:
                ordered.append(summary.id)
        for turn in self.conversation_turns:
            if (
                turn.conversation_session_id is not None
                and turn.conversation_session_id not in ordered
            ):
                ordered.append(turn.conversation_session_id)
        return tuple(ordered)

    @property
    def source_candidate_ids(self) -> tuple[UUID, ...]:
        return tuple(candidate.id for candidate in self.dismissed_candidates)


class JournalistSourceReader(Protocol):
    async def read_session_summaries(
        self,
        *,
        started_at: datetime,
        ended_at: datetime,
        limit: int,
    ) -> tuple[SessionSummaryMaterial, ...]: ...

    async def read_conversation_turns(
        self,
        *,
        started_at: datetime,
        ended_at: datetime,
        limit: int,
    ) -> tuple[ConversationTurnMaterial, ...]: ...

    async def read_ambient_digest(
        self,
        *,
        started_at: datetime,
        ended_at: datetime,
        excerpt_limit: int,
    ) -> AmbientDigest: ...

    async def read_dismissed_candidates(
        self,
        *,
        started_at: datetime,
        ended_at: datetime,
        limit: int,
    ) -> tuple[DismissedCandidateMaterial, ...]: ...


class JournalistInputBuilder:
    def __init__(
        self,
        *,
        reader: JournalistSourceReader,
        session_limit: int = 12,
        turn_limit: int = 80,
        ambient_excerpt_limit: int = 8,
        candidate_limit: int = 12,
    ) -> None:
        self.reader = reader
        self.session_limit = session_limit
        self.turn_limit = turn_limit
        self.ambient_excerpt_limit = ambient_excerpt_limit
        self.candidate_limit = candidate_limit

    async def build(self, diary_date: date) -> JournalistInputSnapshot:
        started_at, ended_at = _utc_day_bounds(diary_date)
        session_summaries = await self.reader.read_session_summaries(
            started_at=started_at,
            ended_at=ended_at,
            limit=self.session_limit,
        )
        conversation_turns = await self.reader.read_conversation_turns(
            started_at=started_at,
            ended_at=ended_at,
            limit=self.turn_limit,
        )
        ambient_digest = await self.reader.read_ambient_digest(
            started_at=started_at,
            ended_at=ended_at,
            excerpt_limit=self.ambient_excerpt_limit,
        )
        dismissed_candidates = await self.reader.read_dismissed_candidates(
            started_at=started_at,
            ended_at=ended_at,
            limit=self.candidate_limit,
        )
        return JournalistInputSnapshot(
            diary_date=diary_date,
            started_at=started_at,
            ended_at=ended_at,
            session_summaries=session_summaries,
            conversation_turns=conversation_turns,
            ambient_digest=ambient_digest,
            dismissed_candidates=dismissed_candidates,
        )


class PostgresJournalistSourceReader:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def read_session_summaries(
        self,
        *,
        started_at: datetime,
        ended_at: datetime,
        limit: int,
    ) -> tuple[SessionSummaryMaterial, ...]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, started_at, ended_at, summary_text
                    FROM conversation_sessions
                    WHERE ended_at >= %s
                      AND ended_at < %s
                      AND summary_status = 'completed'
                      AND summary_text IS NOT NULL
                    ORDER BY ended_at ASC
                    LIMIT %s
                    """,
                    (started_at, ended_at, limit),
                )
                rows = await cur.fetchall()
        return tuple(
            SessionSummaryMaterial(
                id=row[0],
                started_at=row[1],
                ended_at=row[2],
                summary_text=str(row[3]),
            )
            for row in rows
        )

    async def read_conversation_turns(
        self,
        *,
        started_at: datetime,
        ended_at: datetime,
        limit: int,
    ) -> tuple[ConversationTurnMaterial, ...]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        id,
                        conversation_session_id,
                        role,
                        transcript,
                        emotion,
                        status,
                        recorded_at
                    FROM conversation_logs
                    WHERE recorded_at >= %s
                      AND recorded_at < %s
                      AND status IN ('completed', 'interrupted')
                    ORDER BY recorded_at ASC
                    LIMIT %s
                    """,
                    (started_at, ended_at, limit),
                )
                rows = await cur.fetchall()
        return tuple(
            ConversationTurnMaterial(
                id=row[0],
                conversation_session_id=row[1],
                role=str(row[2]),
                text=str(row[3]),
                emotion=row[4],
                status=str(row[5]),
                recorded_at=row[6],
            )
            for row in rows
        )

    async def read_ambient_digest(
        self,
        *,
        started_at: datetime,
        ended_at: datetime,
        excerpt_limit: int,
    ) -> AmbientDigest:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT count(*)
                    FROM ambient_logs
                    WHERE recorded_at >= %s
                      AND recorded_at < %s
                    """,
                    (started_at, ended_at),
                )
                count_row = await cur.fetchone()
                await cur.execute(
                    """
                    SELECT transcript
                    FROM ambient_logs
                    WHERE recorded_at >= %s
                      AND recorded_at < %s
                      AND is_final = TRUE
                      AND length(transcript) BETWEEN 3 AND 80
                    ORDER BY recorded_at ASC
                    LIMIT %s
                    """,
                    (started_at, ended_at, excerpt_limit),
                )
                rows = await cur.fetchall()
        total_count = int(count_row[0]) if count_row is not None else 0
        return AmbientDigest(
            total_count=total_count,
            excerpts=tuple(str(row[0]) for row in rows),
        )

    async def read_dismissed_candidates(
        self,
        *,
        started_at: datetime,
        ended_at: datetime,
        limit: int,
    ) -> tuple[DismissedCandidateMaterial, ...]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, seed, generated_text, priority, dismissed_at
                    FROM utterance_candidates
                    WHERE dismissed_at >= %s
                      AND dismissed_at < %s
                      AND spoken_at IS NULL
                    ORDER BY priority DESC, dismissed_at ASC
                    LIMIT %s
                    """,
                    (started_at, ended_at, limit),
                )
                rows = await cur.fetchall()
        return tuple(
            DismissedCandidateMaterial(
                id=row[0],
                seed=str(row[1]),
                generated_text=row[2],
                priority=float(row[3]),
                dismissed_at=row[4],
            )
            for row in rows
        )


def _utc_day_bounds(diary_date: date) -> tuple[datetime, datetime]:
    started_at = datetime.combine(diary_date, time.min, tzinfo=UTC)
    return started_at, started_at + timedelta(days=1)
