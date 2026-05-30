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
    NOISE_HALLUCINATION_PHRASES = (
        "ご視聴ありがとうございました",
        "ご視聴頂きましてありがとうございました",
        "ご視聴頂きま",
        "字幕をご視聴",
        "お疲れ様です",
        "お疲れさまです",
        "お疲れ様でした",
        "お疲れさまでした",
    )

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
            if _looks_like_low_confidence_followup(ctx):
                return ParticipationDecision(
                    should_participate=False,
                    mode="observer",
                    reason="low_confidence_followup",
                )
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


def _looks_like_low_confidence_followup(ctx: ParticipationContext) -> bool:
    text = ctx.transcript.strip()
    if not text:
        return True
    if len(text) <= 2:
        return True
    if any(phrase in text for phrase in WakeWordJudge.NOISE_HALLUCINATION_PHRASES):
        return True
    if _looks_like_short_unfinished_fragment(text):
        return True
    if _looks_like_unfinished_continuation_tail(text):
        return True
    if ctx.audio_level_db is not None and ctx.audio_level_db <= -30.0 and len(text) <= 20:
        return True
    return False


def _looks_like_short_unfinished_fragment(text: str) -> bool:
    normalized = "".join(text.split()).rstrip("、。？！!?")
    if len(normalized) > 12:
        return False
    unfinished_endings = (
        "の",
        "で",
        "を",
        "が",
        "に",
        "と",
        "は",
        "も",
    )
    return normalized.endswith(unfinished_endings)


def _looks_like_unfinished_continuation_tail(text: str) -> bool:
    stripped = text.strip()
    if stripped.endswith(("。", "？", "?", "！", "!")):
        return False
    normalized = "".join(stripped.split()).rstrip("、,")
    if len(normalized) <= 12:
        return False
    continuation_endings = (
        "さぁ",
        "って",
        "とか",
        "みたいな",
        "という",
        "というか",
        "が",
    )
    return normalized.endswith(continuation_endings)
