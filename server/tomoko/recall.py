from __future__ import annotations

from server.shared.models import SessionSummary
from server.summary.main import embed_text


def rank_summaries_by_relevance(
    summaries: list[SessionSummary],
    query_text: str,
    *,
    top_k: int = 3,
    min_score: float = 0.05,
) -> list[SessionSummary]:
    """query との字面類似で summaries を並べ、関連の高い top_k を返す。

    埋め込み次元が合わない古い行(旧 embed_text で保存されたもの)は
    スコア 0 として扱い、関連が一つも無ければ新しい順の先頭 top_k に落とす。
    """
    query = embed_text(query_text)
    scored = [
        (summary, _cosine(query, tuple(summary.embedding)))
        for summary in summaries
    ]
    relevant = sorted(
        (item for item in scored if item[1] >= min_score),
        key=lambda item: item[1],
        reverse=True,
    )
    if relevant:
        return [summary for summary, _ in relevant[:top_k]]
    return summaries[:top_k]


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)
