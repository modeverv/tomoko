from __future__ import annotations

from typing import Protocol

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
    ) -> None: ...

    async def write_tomoko_turn(
        self,
        *,
        text: str,
        emotion: str,
        device_id: str,
        status: ConversationLogStatus = "completed",
    ) -> None: ...

    async def read_recent_turns(self, *, limit: int) -> list[ConversationTurn]: ...


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
    ) -> None:
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
                        participation_mode
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        transcript.recorded_at,
                        transcript.device_id,
                        transcript.speaker,
                        "user",
                        transcript.text,
                        None,
                        participation_mode,
                    ),
                )

    async def write_tomoko_turn(
        self,
        *,
        text: str,
        emotion: str,
        device_id: str,
        status: ConversationLogStatus = "completed",
    ) -> None:
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
                        status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        device_id,
                        "tomoko",
                        "tomoko",
                        text,
                        emotion,
                        "invited",
                        status,
                    ),
                )

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


class NullConversationLogWriter:
    async def write_user_turn(
        self,
        transcript: Transcript,
        *,
        participation_mode: ParticipationMode,
    ) -> None:
        del transcript, participation_mode
        return None

    async def write_tomoko_turn(
        self,
        *,
        text: str,
        emotion: str,
        device_id: str,
        status: ConversationLogStatus = "completed",
    ) -> None:
        del text, emotion, device_id, status
        return None

    async def read_recent_turns(self, *, limit: int) -> list[ConversationTurn]:
        del limit
        return []
