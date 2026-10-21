from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from server.shared.models import new_id


@dataclass(frozen=True, slots=True)
class SessionBoundaryResult:
    session_id: UUID
    started_new: bool
    closed_session_id: UUID | None = None


@dataclass(slots=True)
class SessionBoundaryModel:
    idle_gap_to_new_session_ms: int = 45_000
    current_session_id: UUID | None = None
    last_activity_at: datetime | None = None

    def observe_utterance(self, uttered_at: datetime) -> SessionBoundaryResult:
        if self.current_session_id is None or self.last_activity_at is None:
            self.current_session_id = new_id()
            self.last_activity_at = uttered_at
            return SessionBoundaryResult(session_id=self.current_session_id, started_new=True)
        gap_ms = (uttered_at - self.last_activity_at).total_seconds() * 1000
        if gap_ms >= self.idle_gap_to_new_session_ms:
            closed = self.current_session_id
            self.current_session_id = new_id()
            self.last_activity_at = uttered_at
            return SessionBoundaryResult(
                session_id=self.current_session_id,
                started_new=True,
                closed_session_id=closed,
            )
        self.last_activity_at = uttered_at
        return SessionBoundaryResult(session_id=self.current_session_id, started_new=False)
