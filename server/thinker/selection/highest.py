from __future__ import annotations

from server.shared.candidate import UtteranceCandidate


class HighestPriority:
    def select(
        self,
        candidates: list[UtteranceCandidate],
    ) -> UtteranceCandidate | None:
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda candidate: (
                -candidate.priority,
                not candidate.urgent,
                candidate.expires_at,
                candidate.created_at,
            ),
        )
