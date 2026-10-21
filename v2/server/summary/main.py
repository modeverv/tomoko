from __future__ import annotations

import zlib
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


def embed_text(text: str, dimensions: int = 64) -> tuple[float, ...]:
    """文字 bigram のハッシュバケット埋め込み。

    軽量な字面類似(トピック語の重なり)用で、意味埋め込みではない。
    本物の埋め込みモデルへの置き換えは別タスク(260704.md 参照)。
    """
    buckets = [0.0 for _ in range(dimensions)]
    compact = "".join(text.split())
    if not compact:
        return tuple(buckets)
    grams = (
        [compact[i : i + 2] for i in range(len(compact) - 1)]
        if len(compact) > 1
        else [compact]
    )
    for gram in grams:
        # プロセス間で安定なハッシュ(str の hash() はシードが揺れる)
        buckets[zlib.crc32(gram.encode("utf-8")) % dimensions] += 1.0
    norm = sum(item * item for item in buckets) ** 0.5 or 1.0
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
