from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

from server.shared.models import ResearchContextHit


@dataclass(frozen=True)
class StoredResearchResult:
    result_id: str
    query: str
    summary_text: str
    provider: str
    fetched_at: datetime
    embedding: list[float]
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
    ) -> None:
        self.rows.append(
            StoredResearchResult(
                result_id=result_id,
                query=query,
                summary_text=summary_text,
                embedding=embedding,
                provider=provider,
                fetched_at=fetched_at or datetime.now(UTC),
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


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return dot / (left_norm * right_norm)
