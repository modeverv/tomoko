from __future__ import annotations

from uuid import UUID

from server.shared.models import SessionSummary


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
