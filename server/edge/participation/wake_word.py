from __future__ import annotations

from server.edge.participation.base import ParticipationContext, ParticipationJudge
from server.shared.models import ParticipationDecision


class WakeWordJudge(ParticipationJudge):
    WAKE_WORDS = (
        "トモコ", 
        "ともこ", 
        "tomoko", 
        "ともく", 
        "トモク", 
        "tomoku",
        "智子",
        "朋子"
    )
    RECALL_WORDS = ("戻って", "話して", "聞いて", "いいよ")

    async def judge(self, ctx: ParticipationContext) -> ParticipationDecision:
        normalized = ctx.transcript.casefold()
        called = any(wake_word in normalized for wake_word in self.WAKE_WORDS)
        if ctx.attention_mode == "withdrawn":
            recalled = called and any(word in normalized for word in self.RECALL_WORDS)
            if recalled:
                return ParticipationDecision(
                    should_participate=True,
                    mode="called",
                    reason="called_back_from_withdrawn",
                )
            return ParticipationDecision(
                should_participate=False,
                mode="withdraw",
                reason="attention_withdrawn",
            )
        if called:
            return ParticipationDecision(
                should_participate=True,
                mode="called",
                reason="wake_word_detected",
            )
        if ctx.attention_mode in {"engaged", "cooldown"} and normalized.strip():
            return ParticipationDecision(
                should_participate=True,
                mode="invited",
                reason=f"attention_{ctx.attention_mode}_followup",
            )
        return ParticipationDecision(
            should_participate=False,
            mode="observer",
            reason="wake_word_not_found",
        )
