from __future__ import annotations

from typing import Protocol

from server.shared.candidate import UtteranceCandidate


class SelectionStrategy(Protocol):
    def select(
        self,
        candidates: list[UtteranceCandidate],
    ) -> UtteranceCandidate | None: ...

