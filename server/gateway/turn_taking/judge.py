from __future__ import annotations

import logging
import re
import time
from typing import Protocol

from server.shared.models import TurnTakingDecision, TurnTakingInput

logger = logging.getLogger(__name__)


class TurnTakingJudge(Protocol):
    async def judge(self, input: TurnTakingInput) -> TurnTakingDecision:
        """Classify whether a new finalized transcript should affect the current reply."""


class RuleFirstTurnTakingJudge:
    STOP_WORDS = (
        "ストップ",
        "止めて",
        "やめて",
        "停止",
        "黙って",
        "だまって",
        "静かにして",
    )
    WAIT_WORDS = (
        "待って",
        "まって",
        "待とう",
        "まとう",
        "ちょっと待って",
        "ちょっとまって",
        "ちょっと待とう",
        "ちょっとまとう",
    )
    RESTART_WORDS = (
        "違う違う",
        "ちがうちがう",
        "いや違う",
        "いやちがう",
        "違う",
        "ちがう",
        "待って待って",
        "まってまって",
    )
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
    DEFER_WORDS = (
        "えっと",
        "あの",
        "その",
        "ちょっと",
    )

    async def judge(self, input: TurnTakingInput) -> TurnTakingDecision:
        started_at = time.perf_counter()
        decision, reason = self.classify(input)
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        return TurnTakingDecision(
            decision=decision,
            reason=reason,
            source="rule",
            elapsed_ms=elapsed_ms,
        )

    def classify(self, input: TurnTakingInput) -> tuple[str, str]:
        text = _normalize(input.new_transcript)
        metrics = input.audio_metrics
        if not text:
            return "continue_current_reply", "empty_transcript"
        if (
            metrics.segment_ms and metrics.segment_ms <= 350
            and metrics.rms_db <= -42
            and metrics.active_frame_ratio <= 0.25
        ):
            return "ignore_as_noise", "short_low_signal"
        if _contains_any(text, self.STOP_WORDS):
            return "stop_speaking", "stop_keyword"
        if _contains_any(text, self.RESTART_WORDS):
            return "restart_with_new_input", "restart_keyword"
        if _contains_any(text, self.WAIT_WORDS):
            return "stop_speaking", "wait_keyword"
        if _is_backchannel(text, self.BACKCHANNELS):
            return "continue_current_reply", "backchannel"
        if input.pending_reply_state == "generating_not_started":
            if _contains_any(text, self.DEFER_WORDS) or len(text) <= 4:
                return "defer_output", "short_possible_continuation"
            return "restart_with_new_input", "confirmed_followup_before_output"
        if len(text) >= 12 or _looks_like_question(input.new_transcript, text):
            return "restart_with_new_input", "substantial_new_input"
        return "continue_current_reply", "small_followup"

    def is_interrupt_candidate(self, text: str) -> bool:
        normalized = _normalize(text)
        return _contains_any(
            normalized,
            self.STOP_WORDS + self.RESTART_WORDS + self.WAIT_WORDS,
        )


def _normalize(text: str) -> str:
    text = text.casefold()
    return re.sub(r"[\s、。！？!?「」『』（）()・,.]+", "", text)


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    return any(_normalize(word) in text for word in words)


def _is_backchannel(text: str, words: tuple[str, ...]) -> bool:
    if text in {_normalize(word) for word in words}:
        return True
    return len(text) <= 8 and _contains_any(text, words)


def _looks_like_question(raw_text: str, normalized_text: str) -> bool:
    if "?" in raw_text or "？" in raw_text:
        return True
    return normalized_text.startswith(("何", "なに", "どう", "なんで", "なぜ"))
