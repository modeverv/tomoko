from __future__ import annotations

from server.edge.participation.base import ParticipationContext, ParticipationJudge
from server.shared.models import ParticipationDecision


class WakeWordJudge(ParticipationJudge):
    WAKE_WORDS = ("トモコ", "ともこ", "tomoko")

    async def judge(self, ctx: ParticipationContext) -> ParticipationDecision:
        normalized = ctx.transcript.casefold()
        called = any(wake_word in normalized for wake_word in self.WAKE_WORDS)
        if called:
            return ParticipationDecision(
                should_participate=True,
                mode="called",
                reason="wake_word_detected",
            )
        return ParticipationDecision(
            should_participate=False,
            mode="observer",
            reason="wake_word_not_found",
        )
