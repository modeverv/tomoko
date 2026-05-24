from __future__ import annotations

from typing import Protocol
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from server.shared.models import (
    PersonaLexiconSnapshot,
    PersonaStateSnapshot,
    PersonaVersionDiff,
)


class PersonaSnapshotStore(Protocol):
    async def find_completed_sessions_without_persona_versions(
        self,
        *,
        limit: int,
    ) -> list[UUID]: ...

    async def read_session_material(
        self,
        *,
        session_id: UUID,
    ) -> tuple[str, list[str]] | None: ...

    async def read_latest_lexicon(self) -> PersonaLexiconSnapshot | None: ...

    async def read_latest_state(self) -> PersonaStateSnapshot | None: ...

    async def write_lexicon_version(
        self,
        *,
        source_session_id: UUID | None,
        reason: str,
        snapshot: PersonaLexiconSnapshot,
        diff: PersonaVersionDiff,
        model: str | None,
        status: str = "completed",
    ) -> UUID: ...

    async def write_state_version(
        self,
        *,
        source_session_id: UUID | None,
        reason: str,
        snapshot: PersonaStateSnapshot,
        diff: PersonaVersionDiff,
        model: str | None,
        status: str = "completed",
    ) -> UUID: ...


class PostgresPersonaSnapshotStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def find_completed_sessions_without_persona_versions(
        self,
        *,
        limit: int,
    ) -> list[UUID]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT s.id
                    FROM conversation_sessions s
                    LEFT JOIN persona_lexicon_versions l
                        ON l.source_session_id = s.id
                       AND l.status = 'completed'
                    LEFT JOIN persona_state_versions p
                        ON p.source_session_id = s.id
                       AND p.status = 'completed'
                    WHERE s.summary_status = 'completed'
                      AND s.summary_text IS NOT NULL
                      AND l.id IS NULL
                      AND p.id IS NULL
                    ORDER BY s.ended_at ASC NULLS LAST, s.started_at ASC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = await cur.fetchall()
        return [row[0] for row in rows]

    async def read_session_material(
        self,
        *,
        session_id: UUID,
    ) -> tuple[str, list[str]] | None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT summary_text
                    FROM conversation_sessions
                    WHERE id = %s
                      AND summary_status = 'completed'
                      AND summary_text IS NOT NULL
                    """,
                    (session_id,),
                )
                session_row = await cur.fetchone()
                if session_row is None:
                    return None

                await cur.execute(
                    """
                    SELECT role, transcript
                    FROM conversation_logs
                    WHERE conversation_session_id = %s
                      AND status = 'completed'
                    ORDER BY recorded_at ASC
                    """,
                    (session_id,),
                )
                turn_rows = await cur.fetchall()

        raw_turns = [
            f"{_speaker_label(role)}: {transcript}"
            for role, transcript in turn_rows
            if role in {"user", "tomoko"}
        ]
        return str(session_row[0]), raw_turns

    async def read_latest_lexicon(self) -> PersonaLexiconSnapshot | None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT lexicon_json
                    FROM persona_lexicon_versions
                    WHERE status = 'completed'
                    ORDER BY version DESC
                    LIMIT 1
                    """
                )
                row = await cur.fetchone()
        if row is None:
            return None
        return PersonaLexiconSnapshot.from_json(row[0])

    async def read_latest_state(self) -> PersonaStateSnapshot | None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT state_json
                    FROM persona_state_versions
                    WHERE status = 'completed'
                    ORDER BY version DESC
                    LIMIT 1
                    """
                )
                row = await cur.fetchone()
        if row is None:
            return None
        return PersonaStateSnapshot.from_json(row[0])

    async def write_lexicon_version(
        self,
        *,
        source_session_id: UUID | None,
        reason: str,
        snapshot: PersonaLexiconSnapshot,
        diff: PersonaVersionDiff,
        model: str | None,
        status: str = "completed",
    ) -> UUID:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    WITH latest AS (
                        SELECT id, version
                        FROM persona_lexicon_versions
                        ORDER BY version DESC
                        LIMIT 1
                    )
                    INSERT INTO persona_lexicon_versions (
                        version,
                        source_session_id,
                        previous_version_id,
                        reason,
                        lexicon_json,
                        diff_json,
                        schema_version,
                        model,
                        status
                    )
                    SELECT
                        COALESCE((SELECT version FROM latest), 0) + 1,
                        %s,
                        (SELECT id FROM latest),
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    RETURNING id
                    """,
                    (
                        source_session_id,
                        reason,
                        Jsonb(snapshot.to_json()),
                        Jsonb(diff.to_json()),
                        snapshot.schema_version,
                        model,
                        status,
                    ),
                )
                row = await cur.fetchone()
                if row is None:
                    raise RuntimeError("persona lexicon insert returned no id")
                return row[0]

    async def write_state_version(
        self,
        *,
        source_session_id: UUID | None,
        reason: str,
        snapshot: PersonaStateSnapshot,
        diff: PersonaVersionDiff,
        model: str | None,
        status: str = "completed",
    ) -> UUID:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    WITH latest AS (
                        SELECT id, version
                        FROM persona_state_versions
                        ORDER BY version DESC
                        LIMIT 1
                    )
                    INSERT INTO persona_state_versions (
                        version,
                        source_session_id,
                        previous_version_id,
                        reason,
                        state_json,
                        diff_json,
                        schema_version,
                        model,
                        status
                    )
                    SELECT
                        COALESCE((SELECT version FROM latest), 0) + 1,
                        %s,
                        (SELECT id FROM latest),
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    RETURNING id
                    """,
                    (
                        source_session_id,
                        reason,
                        Jsonb(snapshot.to_json()),
                        Jsonb(diff.to_json()),
                        snapshot.schema_version,
                        model,
                        status,
                    ),
                )
                row = await cur.fetchone()
                if row is None:
                    raise RuntimeError("persona state insert returned no id")
                return row[0]


class NullPersonaSnapshotStore:
    async def find_completed_sessions_without_persona_versions(
        self,
        *,
        limit: int,
    ) -> list[UUID]:
        del limit
        return []

    async def read_session_material(
        self,
        *,
        session_id: UUID,
    ) -> tuple[str, list[str]] | None:
        del session_id
        return None

    async def read_latest_lexicon(self) -> PersonaLexiconSnapshot | None:
        return None

    async def read_latest_state(self) -> PersonaStateSnapshot | None:
        return None

    async def write_lexicon_version(
        self,
        *,
        source_session_id: UUID | None,
        reason: str,
        snapshot: PersonaLexiconSnapshot,
        diff: PersonaVersionDiff,
        model: str | None,
        status: str = "completed",
    ) -> UUID:
        del source_session_id, reason, snapshot, diff, model, status
        raise RuntimeError("NullPersonaSnapshotStore cannot write lexicon versions")

    async def write_state_version(
        self,
        *,
        source_session_id: UUID | None,
        reason: str,
        snapshot: PersonaStateSnapshot,
        diff: PersonaVersionDiff,
        model: str | None,
        status: str = "completed",
    ) -> UUID:
        del source_session_id, reason, snapshot, diff, model, status
        raise RuntimeError("NullPersonaSnapshotStore cannot write state versions")


def _speaker_label(role: str) -> str:
    if role == "tomoko":
        return "トモコ"
    return "ユーザー"
