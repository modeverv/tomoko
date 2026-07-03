from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from server.shared.models import SessionSummary
from server.tomoko.db_bridge import (
    insert_session_summary_sql,
    insert_summary_embedding_sql,
    load_session_utterance_texts,
    load_unsummarized_closed_session_ids,
)


@dataclass(frozen=True, slots=True)
class SummaryMaterializationResult:
    sessions_read: int
    summaries_upserted: int


def summarize_session(session_id: UUID, utterances: list[str]) -> SessionSummary:
    joined = " ".join(utterances).strip()
    keyword = joined.split()[0] if joined.split() else "empty"
    conclusion = joined[:80] if joined else "会話内容なし"
    return SessionSummary(
        session_id=session_id,
        keyword=keyword,
        conclusion=conclusion,
        embedding=embed_text(keyword + " " + conclusion),
    )


def embed_text(text: str, dimensions: int = 8) -> tuple[float, ...]:
    buckets = [0.0 for _ in range(dimensions)]
    for index, char in enumerate(text):
        buckets[index % dimensions] += (ord(char) % 97) / 97.0
    norm = sum(abs(item) for item in buckets) or 1.0
    return tuple(item / norm for item in buckets)


async def materialize_summaries_from_db(
    conn: object,
    *,
    limit: int = 4,
) -> SummaryMaterializationResult:
    session_ids = await load_unsummarized_closed_session_ids(conn, limit=limit)
    upserted = 0
    for session_id in session_ids:
        utterances = await load_session_utterance_texts(conn, session_id)
        summary = summarize_session(session_id, utterances)
        summary_command = insert_session_summary_sql(summary)
        await conn.execute(summary_command.query, summary_command.params)
        embedding_command = insert_summary_embedding_sql(summary)
        await conn.execute(embedding_command.query, embedding_command.params)
        upserted += 1
    return SummaryMaterializationResult(
        sessions_read=len(session_ids),
        summaries_upserted=upserted,
    )
