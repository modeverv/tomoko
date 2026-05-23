from __future__ import annotations

from typing import Protocol

import psycopg

from server.shared.models import Transcript


class AmbientLogWriter(Protocol):
    async def write(self, transcript: Transcript, *, tomoko_participated: bool) -> None: ...


class PostgresAmbientLogWriter:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def write(self, transcript: Transcript, *, tomoko_participated: bool) -> None:
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
                        tomoko_participated
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        transcript.recorded_at,
                        transcript.device_id,
                        transcript.speaker,
                        transcript.text,
                        transcript.audio_level_db,
                        transcript.is_final,
                        tomoko_participated,
                    ),
                )


class NullAmbientLogWriter:
    async def write(self, transcript: Transcript, *, tomoko_participated: bool) -> None:
        return None
