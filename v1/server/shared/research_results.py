from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import psycopg
from psycopg.types.json import Jsonb

from server.shared.memory import _to_vector_literal
from server.shared.models import ResearchContextHit


class ResearchResultStore(Protocol):
    async def insert(
        self,
        *,
        result_id: str,
        query: str,
        summary_text: str,
        embedding: list[float],
        provider: str,
        fetched_at: datetime | None = None,
        short_answer: str = "",
        citation_urls: tuple[str, ...] = (),
        raw_artifact_path: str | None = None,
        embedding_model: str = "",
    ) -> None: ...

    async def search_similar(
        self,
        *,
        embedding: list[float],
        limit: int,
    ) -> list[ResearchContextHit]: ...


@dataclass(frozen=True)
class StoredResearchResult:
    result_id: str
    query: str
    summary_text: str
    provider: str
    fetched_at: datetime
    embedding: list[float]
    embedding_model: str = ""
    short_answer: str = ""
    citation_urls: tuple[str, ...] = ()
    raw_artifact_path: str | None = None


class InMemoryResearchResultStore:
    def __init__(self) -> None:
        self.rows: list[StoredResearchResult] = []

    async def insert(
        self,
        *,
        result_id: str,
        query: str,
        summary_text: str,
        embedding: list[float],
        provider: str,
        fetched_at: datetime | None = None,
        short_answer: str = "",
        citation_urls: tuple[str, ...] = (),
        raw_artifact_path: str | None = None,
        embedding_model: str = "",
    ) -> None:
        self.rows.append(
            StoredResearchResult(
                result_id=result_id,
                query=query,
                summary_text=summary_text,
                embedding=embedding,
                provider=provider,
                fetched_at=fetched_at or datetime.now(UTC),
                embedding_model=embedding_model,
                short_answer=short_answer,
                citation_urls=citation_urls,
                raw_artifact_path=raw_artifact_path,
            )
        )

    async def search_similar(
        self,
        *,
        embedding: list[float],
        limit: int,
    ) -> list[ResearchContextHit]:
        scored = [
            (
                _cosine_similarity(embedding, row.embedding),
                row,
            )
            for row in self.rows
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            ResearchContextHit(
                result_id=row.result_id,
                query=row.query,
                summary_text=row.summary_text,
                provider=row.provider,
                fetched_at=row.fetched_at,
                similarity=similarity,
                citation_urls=row.citation_urls,
                raw_artifact_path=row.raw_artifact_path,
            )
            for similarity, row in scored[:limit]
        ]


class PostgresResearchResultStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def insert(
        self,
        *,
        result_id: str,
        query: str,
        summary_text: str,
        embedding: list[float],
        provider: str,
        fetched_at: datetime | None = None,
        short_answer: str = "",
        citation_urls: tuple[str, ...] = (),
        raw_artifact_path: str | None = None,
        embedding_model: str = "",
    ) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO research_results (
                        id,
                        query,
                        summary_text,
                        summary_embedding,
                        summary_embedding_model,
                        short_answer,
                        provider,
                        fetched_at,
                        citation_urls,
                        raw_artifact_path
                    )
                    VALUES (%s, %s, %s, %s::vector, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id)
                    DO UPDATE SET
                        query = EXCLUDED.query,
                        summary_text = EXCLUDED.summary_text,
                        summary_embedding = EXCLUDED.summary_embedding,
                        summary_embedding_model = EXCLUDED.summary_embedding_model,
                        short_answer = EXCLUDED.short_answer,
                        provider = EXCLUDED.provider,
                        fetched_at = EXCLUDED.fetched_at,
                        citation_urls = EXCLUDED.citation_urls,
                        raw_artifact_path = EXCLUDED.raw_artifact_path,
                        updated_at = now()
                    """,
                    (
                        result_id,
                        query,
                        summary_text,
                        _to_vector_literal(embedding),
                        embedding_model,
                        short_answer,
                        provider,
                        fetched_at or datetime.now(UTC),
                        Jsonb(list(citation_urls)),
                        raw_artifact_path,
                    ),
                )

    async def search_similar(
        self,
        *,
        embedding: list[float],
        limit: int,
    ) -> list[ResearchContextHit]:
        vector = _to_vector_literal(embedding)
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        id,
                        query,
                        summary_text,
                        provider,
                        fetched_at,
                        1 - (summary_embedding <=> %s::vector) AS similarity,
                        citation_urls,
                        raw_artifact_path
                    FROM research_results
                    WHERE summary_embedding IS NOT NULL
                    ORDER BY summary_embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (vector, vector, limit),
                )
                rows = await cur.fetchall()

        return [
            ResearchContextHit(
                result_id=result_id,
                query=query,
                summary_text=summary_text,
                provider=provider,
                fetched_at=fetched_at,
                similarity=float(similarity),
                citation_urls=_citation_urls_tuple(citation_urls),
                raw_artifact_path=raw_artifact_path,
            )
            for (
                result_id,
                query,
                summary_text,
                provider,
                fetched_at,
                similarity,
                citation_urls,
                raw_artifact_path,
            ) in rows
        ]


def _citation_urls_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return dot / (left_norm * right_norm)
