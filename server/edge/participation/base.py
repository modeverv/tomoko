from __future__ import annotations

from abc import ABC, abstractmethod

from server.shared.models import ParticipationContext, ParticipationDecision


class ParticipationJudge(ABC):
    @abstractmethod
    async def judge(self, ctx: ParticipationContext) -> ParticipationDecision: ...
