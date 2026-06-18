from __future__ import annotations

from dataclasses import dataclass

from server.shared.models import FloorSignal, SpeechDecision, SpeechDecisionKind


@dataclass(frozen=True, slots=True)
class SpeechDecisionModel:
    full_reply_silence_ms: int = 500
    initiative_silence_ms: int = 8000
    candidate_pressure_threshold: float = 0.65

    def decide(self, signal: FloorSignal, *, log_only_initiatives: bool = True) -> SpeechDecision:
        if signal.stop_requested:
            return SpeechDecision(
                decision=SpeechDecisionKind.STOP,
                should_execute=True,
                reason="stop requested",
                score_breakdown={"stop": 1.0},
            )
        if signal.user_speaking:
            return SpeechDecision(
                decision=SpeechDecisionKind.YIELD_FLOOR,
                should_execute=False,
                reason="user is speaking",
                score_breakdown={"user_speaking": 1.0},
            )
        if signal.tomoko_speaking or signal.playback_active:
            return SpeechDecision(
                decision=SpeechDecisionKind.PREPARE_ONLY,
                should_execute=False,
                reason="tomoko is already speaking",
                score_breakdown={"playback": 1.0},
            )
        yield_score = signal.p_yielding if signal.p_yielding is not None else 0.0
        full_reply_score = min(1.0, signal.silence_ms / self.full_reply_silence_ms) * (
            0.5 + 0.5 * yield_score
        )
        if signal.silence_ms >= self.full_reply_silence_ms and full_reply_score >= 0.5:
            return SpeechDecision(
                decision=SpeechDecisionKind.FULL_REPLY,
                should_execute=True,
                reason="floor appears available after final utterance",
                score_breakdown={"full_reply": full_reply_score, "p_yielding": yield_score},
            )
        initiative_score = (
            min(1.0, signal.silence_ms / self.initiative_silence_ms)
            * signal.candidate_pressure
            * (1.0 if signal.user_present else 0.0)
        )
        if initiative_score >= self.candidate_pressure_threshold:
            return SpeechDecision(
                decision=SpeechDecisionKind.INITIATIVE,
                should_execute=not log_only_initiatives,
                log_only=log_only_initiatives,
                reason="candidate pressure is high during idle gap",
                score_breakdown={"initiative": initiative_score},
            )
        return SpeechDecision(
            decision=SpeechDecisionKind.SILENCE,
            should_execute=False,
            reason="floor is not available enough",
            score_breakdown={"full_reply": full_reply_score, "initiative": initiative_score},
        )
