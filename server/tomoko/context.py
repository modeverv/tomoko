from __future__ import annotations

import time
from dataclasses import dataclass, field
from uuid import UUID

from server.shared.models import (
    CandidateRecord,
    ContextSnapshot,
    SessionSummary,
    UserStatusObservation,
)


@dataclass(slots=True)
class ContextSnapshotBuilderV2:
    calendar_ttl_sec: float = 60.0
    _calendar_cache: dict[UUID | None, tuple[float, dict[str, str]]] = field(default_factory=dict)

    def build(
        self,
        *,
        session_id: UUID | None,
        recent_utterances: list[str],
        summaries: list[SessionSummary],
        calendar_loader: callable,
        user_status: UserStatusObservation | None,
        candidates: list[CandidateRecord],
    ) -> ContextSnapshot:
        started = time.perf_counter()
        calendar_items = self._load_calendar(session_id, calendar_loader)
        return ContextSnapshot(
            session_id=session_id,
            recent_utterances=tuple(recent_utterances),
            summaries=tuple(summaries),
            calendar_items=calendar_items,
            user_status=user_status,
            candidates=tuple(candidates),
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )

    def _load_calendar(self, session_id: UUID | None, loader: callable) -> dict[str, str]:
        now = time.monotonic()
        cached = self._calendar_cache.get(session_id)
        if cached and now - cached[0] <= self.calendar_ttl_sec:
            return cached[1]
        loaded = dict(loader())
        self._calendar_cache[session_id] = (now, loaded)
        return loaded
