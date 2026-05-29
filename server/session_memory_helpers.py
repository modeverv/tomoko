from __future__ import annotations

from server.shared.models import MemoryHit, SessionSummaryHit


def session_summary_hit_to_memory(hit: SessionSummaryHit) -> MemoryHit:
    return MemoryHit(
        speaker="tomoko",
        text=f"会話セッション要約: {hit.summary_text}",
        timestamp=hit.ended_at or hit.started_at,
        similarity=hit.similarity,
        source_id=f"session_summary:{hit.session_id}",
    )
