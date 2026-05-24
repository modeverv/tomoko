from __future__ import annotations

from typing import Protocol
from uuid import UUID

import psycopg

from server.shared.inference.embedding.base import EmbeddingBackend
from server.shared.models import MemoryHit


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


def _to_vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.8g}" for value in values) + "]"
