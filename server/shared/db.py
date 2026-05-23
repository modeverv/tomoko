from __future__ import annotations

from typing import Protocol

import psycopg

from server.shared.models import AttentionMode, ParticipationMode, Transcript


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

    async def write_tomoko_turn(self, *, text: str, emotion: str, device_id: str) -> None: ...


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

    async def write_tomoko_turn(self, *, text: str, emotion: str, device_id: str) -> None:
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
                        participation_mode
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        device_id,
                        "tomoko",
                        "tomoko",
                        text,
                        emotion,
                        "invited",
                    ),
                )


class NullConversationLogWriter:
    async def write_user_turn(
        self,
        transcript: Transcript,
        *,
        participation_mode: ParticipationMode,
    ) -> None:
        del transcript, participation_mode
        return None

    async def write_tomoko_turn(self, *, text: str, emotion: str, device_id: str) -> None:
        del text, emotion, device_id
        return None
