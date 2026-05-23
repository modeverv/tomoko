from __future__ import annotations

import re
from difflib import SequenceMatcher

from server.shared.models import BargeInContext, BargeInDecision


class BargeInDetector:
    STARTUP_GRACE_MS = 300
    ECHO_THRESHOLD = 0.72
    MIN_ECHO_CHARS = 4

    BACKCHANNELS = (
        "うん",
        "はい",
        "へえ",
        "へー",
        "なるほど",
        "そうなんだ",
        "そうだね",
        "たしかに",
    )
    SOFT_INTERRUPTS = (
        "ちょっと待って",
        "ちょっとまって",
        "待って",
        "まって",
        "違う",
        "ちがう",
        "それ違う",
    )
    HARD_INTERRUPTS = (
        "待って待って",
        "まってまって",
        "違う違う",
        "ちがうちがう",
        "ストップ",
        "止めて",
        "やめて",
        "停止",
    )
    QUESTION_MARKERS = ("?", "？")
    QUESTION_PREFIXES = (
        "何",
        "なに",
        "どう",
        "いつ",
        "どこ",
        "誰",
        "だれ",
        "なんで",
        "なぜ",
    )

    def classify(self, ctx: BargeInContext) -> BargeInDecision:
        transcript = _normalize(ctx.transcript)
        recent_tomoko_text = _normalize(ctx.recent_tomoko_text)
        if not transcript:
            return BargeInDecision(
                kind="backchannel",
                action="continue_speaking",
                reason="empty_transcript",
            )

        if self._is_echo(transcript, recent_tomoko_text):
            return BargeInDecision(
                kind="echo",
                action="continue_speaking",
                reason="matches_recent_tomoko_text",
            )

        if _contains_any(transcript, self.HARD_INTERRUPTS):
            return BargeInDecision(
                kind="hard_interrupt",
                action="restart_turn",
                reason="hard_interrupt_keyword",
            )

        if ctx.speaking_elapsed_ms < self.STARTUP_GRACE_MS:
            return BargeInDecision(
                kind="backchannel",
                action="continue_speaking",
                reason="startup_grace",
            )

        if self._is_backchannel(transcript):
            return BargeInDecision(
                kind="backchannel",
                action="continue_speaking",
                reason="backchannel_keyword",
            )

        if _contains_any(transcript, self.SOFT_INTERRUPTS):
            return BargeInDecision(
                kind="soft_interrupt",
                action="finish_sentence",
                reason="soft_interrupt_keyword",
            )

        if _is_question(ctx.transcript, transcript, self.QUESTION_PREFIXES):
            return BargeInDecision(
                kind="new_question",
                action="finish_sentence",
                reason="question_while_tomoko_speaking",
            )

        return BargeInDecision(
            kind="new_question",
            action="finish_sentence",
            reason="speech_while_tomoko_speaking",
        )

    def _is_echo(self, transcript: str, recent_tomoko_text: str) -> bool:
        if (
            len(transcript) < self.MIN_ECHO_CHARS
            or len(recent_tomoko_text) < self.MIN_ECHO_CHARS
        ):
            return False
        if transcript in recent_tomoko_text or recent_tomoko_text in transcript:
            return True
        ratio = SequenceMatcher(None, transcript, recent_tomoko_text).ratio()
        return ratio >= self.ECHO_THRESHOLD

    def _is_backchannel(self, transcript: str) -> bool:
        if transcript in self.BACKCHANNELS:
            return True
        return len(transcript) <= 8 and _contains_any(transcript, self.BACKCHANNELS)


def _normalize(text: str) -> str:
    text = text.casefold()
    return re.sub(r"[\s、。！？!?「」『』（）()・,.]+", "", text)


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    return any(_normalize(word) in text for word in words)


def _is_question(
    raw_transcript: str,
    normalized_transcript: str,
    prefixes: tuple[str, ...],
) -> bool:
    if any(marker in raw_transcript for marker in BargeInDetector.QUESTION_MARKERS):
        return True
    return any(normalized_transcript.startswith(_normalize(prefix)) for prefix in prefixes)
