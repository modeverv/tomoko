from __future__ import annotations

from typing import Protocol
from uuid import UUID

import psycopg

from server.shared.inference.embedding.base import EmbeddingBackend
from server.shared.models import ConversationTurn, MemoryHit, SessionSummaryHit


class ConversationMemoryStore(Protocol):
    async def write_embedding(
        self,
        *,
        conversation_log_id: UUID,
        embedding: list[float],
        model: str,
    ) -> None: ...

    async def search_similar(
        self,
        *,
        embedding: list[float],
        limit: int,
    ) -> list[MemoryHit]: ...

    async def embed_missing_turns(
        self,
        *,
        embedding_backend: EmbeddingBackend,
        limit: int = 100,
    ) -> int: ...


class ConversationSessionSummaryStore(Protocol):
    async def claim_pending_sessions(self, *, limit: int) -> list[UUID]: ...

    async def read_session_turns(self, *, session_id: UUID) -> list[ConversationTurn]: ...

    async def complete_summary(
        self,
        *,
        session_id: UUID,
        summary_text: str,
        summary_model: str,
        embedding: list[float],
        embedding_model: str,
    ) -> None: ...

    async def mark_summary_error(self, *, session_id: UUID, error: str) -> None: ...

    async def search_similar_summaries(
        self,
        *,
        embedding: list[float],
        limit: int,
    ) -> list[SessionSummaryHit]: ...


class PostgresConversationMemoryStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def write_embedding(
        self,
        *,
        conversation_log_id: UUID,
        embedding: list[float],
        model: str,
    ) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO conversation_embeddings (
                        conversation_log_id,
                        embedding,
                        model
                    )
                    VALUES (%s, %s::vector, %s)
                    ON CONFLICT (conversation_log_id)
                    DO UPDATE SET
                        embedding = EXCLUDED.embedding,
                        model = EXCLUDED.model,
                        embedded_at = now()
                    """,
                    (conversation_log_id, _to_vector_literal(embedding), model),
                )

    async def search_similar(
        self,
        *,
        embedding: list[float],
        limit: int,
    ) -> list[MemoryHit]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        c.role,
                        c.transcript,
                        c.recorded_at,
                        c.emotion,
                        1 - (e.embedding <=> %s::vector) AS similarity
                    FROM conversation_embeddings e
                    JOIN conversation_logs c ON c.id = e.conversation_log_id
                    WHERE c.status = 'completed'
                    ORDER BY e.embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (
                        _to_vector_literal(embedding),
                        _to_vector_literal(embedding),
                        limit,
                    ),
                )
                rows = await cur.fetchall()

        hits: list[MemoryHit] = []
        for role, transcript, recorded_at, emotion, similarity in rows:
            if role not in {"user", "tomoko"}:
                continue
            hits.append(
                MemoryHit(
                    speaker=role,
                    text=transcript,
                    timestamp=recorded_at,
                    emotion=emotion,
                    similarity=float(similarity),
                )
            )
        return hits

    async def embed_missing_turns(
        self,
        *,
        embedding_backend: EmbeddingBackend,
        limit: int = 100,
    ) -> int:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT c.id, c.transcript
                    FROM conversation_logs c
                    LEFT JOIN conversation_embeddings e ON e.conversation_log_id = c.id
                    WHERE c.status = 'completed'
                      AND c.transcript <> ''
                      AND e.conversation_log_id IS NULL
                    ORDER BY c.recorded_at ASC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = await cur.fetchall()

        for conversation_log_id, transcript in rows:
            embedding = await embedding_backend.embed_passage(transcript)
            await self.write_embedding(
                conversation_log_id=conversation_log_id,
                embedding=embedding,
                model=embedding_backend.model,
            )
        return len(rows)


class NullConversationMemoryStore:
    async def write_embedding(
        self,
        *,
        conversation_log_id: UUID,
        embedding: list[float],
        model: str,
    ) -> None:
        del conversation_log_id, embedding, model
        return None

    async def search_similar(
        self,
        *,
        embedding: list[float],
        limit: int,
    ) -> list[MemoryHit]:
        del embedding, limit
        return []

    async def embed_missing_turns(
        self,
        *,
        embedding_backend: EmbeddingBackend,
        limit: int = 100,
    ) -> int:
        del embedding_backend, limit
        return 0


class PostgresConversationSessionSummaryStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def claim_pending_sessions(self, *, limit: int) -> list[UUID]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE conversation_sessions
                    SET summary_status = 'processing',
                        summary_error = NULL
                    WHERE id IN (
                        SELECT id
                        FROM conversation_sessions
                        WHERE summary_status = 'pending'
                          AND ended_at IS NOT NULL
                        ORDER BY ended_at ASC
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING id
                    """,
                    (limit,),
                )
                rows = await cur.fetchall()
        return [row[0] for row in rows]

    async def read_session_turns(self, *, session_id: UUID) -> list[ConversationTurn]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT role, transcript, recorded_at, emotion
                    FROM conversation_logs
                    WHERE conversation_session_id = %s
                      AND status = 'completed'
                    ORDER BY recorded_at ASC
                    """,
                    (session_id,),
                )
                rows = await cur.fetchall()

        turns: list[ConversationTurn] = []
        for role, transcript, recorded_at, emotion in rows:
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

    async def complete_summary(
        self,
        *,
        session_id: UUID,
        summary_text: str,
        summary_model: str,
        embedding: list[float],
        embedding_model: str,
    ) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE conversation_sessions
                    SET summary_text = %s,
                        summary_model = %s,
                        summary_generated_at = now(),
                        summary_embedding = %s::vector,
                        summary_embedding_model = %s,
                        summary_embedded_at = now(),
                        summary_status = 'completed',
                        summary_error = NULL
                    WHERE id = %s
                    """,
                    (
                        summary_text,
                        summary_model,
                        _to_vector_literal(embedding),
                        embedding_model,
                        session_id,
                    ),
                )

    async def mark_summary_error(self, *, session_id: UUID, error: str) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE conversation_sessions
                    SET summary_status = 'error',
                        summary_error = %s
                    WHERE id = %s
                    """,
                    (error, session_id),
                )

    async def search_similar_summaries(
        self,
        *,
        embedding: list[float],
        limit: int,
    ) -> list[SessionSummaryHit]:
        vector = _to_vector_literal(embedding)
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        id,
                        summary_text,
                        started_at,
                        ended_at,
                        1 - (summary_embedding <=> %s::vector) AS similarity
                    FROM conversation_sessions
                    WHERE summary_status = 'completed'
                      AND summary_text IS NOT NULL
                      AND summary_embedding IS NOT NULL
                    ORDER BY summary_embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (vector, vector, limit),
                )
                rows = await cur.fetchall()

        return [
            SessionSummaryHit(
                session_id=session_id,
                summary_text=summary_text,
                started_at=started_at,
                ended_at=ended_at,
                similarity=float(similarity),
            )
            for session_id, summary_text, started_at, ended_at, similarity in rows
        ]


class NullConversationSessionSummaryStore:
    async def claim_pending_sessions(self, *, limit: int) -> list[UUID]:
        del limit
        return []

    async def read_session_turns(self, *, session_id: UUID) -> list[ConversationTurn]:
        del session_id
        return []

    async def complete_summary(
        self,
        *,
        session_id: UUID,
        summary_text: str,
        summary_model: str,
        embedding: list[float],
        embedding_model: str,
    ) -> None:
        del session_id, summary_text, summary_model, embedding, embedding_model
        return None

    async def mark_summary_error(self, *, session_id: UUID, error: str) -> None:
        del session_id, error
        return None

    async def search_similar_summaries(
        self,
        *,
        embedding: list[float],
        limit: int,
    ) -> list[SessionSummaryHit]:
        del embedding, limit
        return []


def _to_vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.8g}" for value in values) + "]"
