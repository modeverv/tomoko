from __future__ import annotations

from typing import Protocol

from server.shared.candidate import CandidateSeed, ThinkerSourceContext


class InformationSource(Protocol):
    async def collect(self, context: ThinkerSourceContext) -> list[CandidateSeed]: ...

