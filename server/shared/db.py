from __future__ import annotations

from typing import Protocol
from uuid import UUID

import psycopg

from server.shared.models import (
    AttentionMode,
    ConversationLogStatus,
    ConversationTurn,
    ParticipationMode,
    Transcript,
)


class AmbientLogWriter(Protocol):
    async def write(
        self,
        transcript: Transcript,
        *,
        tomoko_participated: bool,
        attention_mode: AttentionMode,
        attended: bool,
        participation_mode: ParticipationMode,
    ) -> None: ...


class ConversationLogWriter(Protocol):
    async def write_user_turn(
        self,
        transcript: Transcript,
        *,
        participation_mode: ParticipationMode,
        conversation_session_id: UUID | None = None,
    ) -> UUID | None: ...

    async def write_tomoko_turn(
        self,
        *,
        text: str,
        emotion: str,
        device_id: str,
        status: ConversationLogStatus = "completed",
        conversation_session_id: UUID | None = None,
    ) -> UUID | None: ...

    async def read_recent_turns(self, *, limit: int) -> list[ConversationTurn]: ...

    async def read_recent_turns_for_session(
        self,
        *,
        conversation_session_id: UUID,
        limit: int,
    ) -> list[ConversationTurn]: ...


class ConversationSessionStore(Protocol):
    async def create_session(self, *, device_id: str, start_reason: str) -> UUID: ...

    async def close_session(self, session_id: UUID, *, end_reason: str) -> None: ...


class PostgresAmbientLogWriter:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def write(
        self,
        transcript: Transcript,
        *,
        tomoko_participated: bool,
        attention_mode: AttentionMode,
        attended: bool,
        participation_mode: ParticipationMode,
    ) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO ambient_logs (
                        recorded_at,
                        device_id,
                        speaker,
                        transcript,
                        audio_level_db,
                        is_final,
                        tomoko_participated,
                        attention_mode,
                        attended,
                        participation_mode
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        transcript.recorded_at,
                        transcript.device_id,
                        transcript.speaker,
                        transcript.text,
                        transcript.audio_level_db,
                        transcript.is_final,
                        tomoko_participated,
                        attention_mode,
                        attended,
                        participation_mode,
                    ),
                )


class NullAmbientLogWriter:
    async def write(
        self,
        transcript: Transcript,
        *,
        tomoko_participated: bool,
        attention_mode: AttentionMode,
        attended: bool,
        participation_mode: ParticipationMode,
    ) -> None:
        del transcript, tomoko_participated, attention_mode, attended, participation_mode
        return None


class PostgresConversationLogWriter:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def write_user_turn(
        self,
        transcript: Transcript,
        *,
        participation_mode: ParticipationMode,
        conversation_session_id: UUID | None = None,
    ) -> UUID | None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO conversation_logs (
                        recorded_at,
                        device_id,
                        speaker,
                        role,
                        transcript,
                        emotion,
                        participation_mode,
                        conversation_session_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        transcript.recorded_at,
                        transcript.device_id,
                        transcript.speaker,
                        "user",
                        transcript.text,
                        None,
                        participation_mode,
                        conversation_session_id,
                    ),
                )
                row = await cur.fetchone()
                return row[0] if row is not None else None

    async def write_tomoko_turn(
        self,
        *,
        text: str,
        emotion: str,
        device_id: str,
        status: ConversationLogStatus = "completed",
        conversation_session_id: UUID | None = None,
    ) -> UUID | None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO conversation_logs (
                        device_id,
                        speaker,
                        role,
                        transcript,
                        emotion,
                        participation_mode,
                        status,
                        conversation_session_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        device_id,
                        "tomoko",
                        "tomoko",
                        text,
                        emotion,
                        "invited",
                        status,
                        conversation_session_id,
                    ),
                )
                row = await cur.fetchone()
                return row[0] if row is not None else None

    async def read_recent_turns(self, *, limit: int) -> list[ConversationTurn]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT role, transcript, recorded_at, emotion
                    FROM conversation_logs
                    WHERE status = 'completed'
                    ORDER BY recorded_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = await cur.fetchall()

        turns: list[ConversationTurn] = []
        for role, transcript, recorded_at, emotion in reversed(rows):
            if role not in {"user", "tomoko"}:
                continue
            turns.append(
                ConversationTurn(
                    speaker=role,
                    text=transcript,
                    timestamp=recorded_at,
                    emotion=emotion,
                )
            )
        return turns

    async def read_recent_turns_for_session(
        self,
        *,
        conversation_session_id: UUID,
        limit: int,
    ) -> list[ConversationTurn]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT role, transcript, recorded_at, emotion
                    FROM conversation_logs
                    WHERE status = 'completed'
                      AND conversation_session_id = %s
                    ORDER BY recorded_at DESC
                    LIMIT %s
                    """,
                    (conversation_session_id, limit),
                )
                rows = await cur.fetchall()

        turns: list[ConversationTurn] = []
        for role, transcript, recorded_at, emotion in reversed(rows):
            if role not in {"user", "tomoko"}:
                continue
            turns.append(
                ConversationTurn(
                    speaker=role,
                    text=transcript,
                    timestamp=recorded_at,
                    emotion=emotion,
                )
            )
        return turns


class PostgresConversationSessionStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def create_session(self, *, device_id: str, start_reason: str) -> UUID:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO conversation_sessions (
                        device_id,
                        start_reason,
                        summary_status
                    )
                    VALUES (%s, %s, 'not_ready')
                    RETURNING id
                    """,
                    (device_id, start_reason),
                )
                row = await cur.fetchone()
                if row is None:
                    raise RuntimeError("conversation session insert returned no id")
                return row[0]

    async def close_session(self, session_id: UUID, *, end_reason: str) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE conversation_sessions
                    SET ended_at = COALESCE(ended_at, now()),
                        end_reason = COALESCE(end_reason, %s),
                        summary_status = CASE
                            WHEN summary_status = 'not_ready' THEN 'pending'
                            ELSE summary_status
                        END
                    WHERE id = %s
                    """,
                    (end_reason, session_id),
                )


class NullConversationLogWriter:
    async def write_user_turn(
        self,
        transcript: Transcript,
        *,
        participation_mode: ParticipationMode,
        conversation_session_id: UUID | None = None,
    ) -> UUID | None:
        del transcript, participation_mode, conversation_session_id
        return None

    async def write_tomoko_turn(
        self,
        *,
        text: str,
        emotion: str,
        device_id: str,
        status: ConversationLogStatus = "completed",
        conversation_session_id: UUID | None = None,
    ) -> UUID | None:
        del text, emotion, device_id, status, conversation_session_id
        return None

    async def read_recent_turns(self, *, limit: int) -> list[ConversationTurn]:
        del limit
        return []

    async def read_recent_turns_for_session(
        self,
        *,
        conversation_session_id: UUID,
        limit: int,
    ) -> list[ConversationTurn]:
        del conversation_session_id, limit
        return []


class NullConversationSessionStore:
    async def create_session(self, *, device_id: str, start_reason: str) -> UUID:
        del device_id, start_reason
        raise RuntimeError("NullConversationSessionStore cannot create sessions")

    async def close_session(self, session_id: UUID, *, end_reason: str) -> None:
        del session_id, end_reason
        return None
