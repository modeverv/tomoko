from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime

from server.shared.logging import JsonlLogger
from server.shared.models import (
    SpeechPressureState,
    SpeechSchedulerAction,
    SpeechSchedulerInput,
    SpeechSchedulerOutput,
    SpeechSchedulerThresholds,
    SpeechSchedulerWeights,
    SpeechTextIntent,
)

STOP_CUES = ("止めて", "やめて", "ストップ", "黙って", "もういい", "stop")


@dataclass(slots=True)
class SpeechScheduler:
    weights: SpeechSchedulerWeights = field(default_factory=SpeechSchedulerWeights)
    thresholds: SpeechSchedulerThresholds = field(default_factory=SpeechSchedulerThresholds)
    logger: JsonlLogger | None = None
    decay_tau_sec: float = 20.0

    def decide(self, scheduler_input: SpeechSchedulerInput) -> SpeechSchedulerOutput:
        pressure = decayed_pressure(scheduler_input.pressure_state, self.decay_tau_sec)
        stop_intent = max(
            scheduler_input.stop_intent,
            detect_stop_intent(_input_text(scheduler_input)),
        )
        pressure.reply_pressure += scheduler_input.semantic_saturation
        pressure.initiative_pressure += scheduler_input.candidate_pressure
        pressure.calendar_pressure += scheduler_input.calendar_urgency
        pressure.curiosity_pressure += scheduler_input.curiosity_pressure
        pressure.recent_rejection_penalty += scheduler_input.recent_rejection_penalty
        pressure.fatigue += scheduler_input.fatigue
        if scheduler_input.user_speaking and scheduler_input.tomoko_currently_speaking:
            pressure.interruption_penalty += 0.7

        score_breakdown = {
            "reply": self.weights.reply_weight * pressure.reply_pressure,
            "initiative": self.weights.initiative_weight * pressure.initiative_pressure,
            "calendar": self.weights.calendar_weight * pressure.calendar_pressure,
            "curiosity": self.weights.curiosity_weight * pressure.curiosity_pressure,
            "memory": self.weights.memory_weight * scheduler_input.memory_relevance,
            "saturation": self.weights.saturation_weight * scheduler_input.semantic_saturation,
            "maai": self.weights.maai_weight * (scheduler_input.p_yielding or 0.0),
            "interruption_penalty": -self.weights.interruption_penalty_weight
            * pressure.interruption_penalty,
            "recent_rejection_penalty": -self.weights.rejection_penalty_weight
            * pressure.recent_rejection_penalty,
            "fatigue": -self.weights.fatigue_weight * pressure.fatigue,
        }
        score = sum(score_breakdown.values())
        text_intent = self._text_intent(scheduler_input, pressure, stop_intent)
        action, reason = self._select_action(
            scheduler_input,
            score,
            pressure.interruption_penalty,
            stop_intent,
            text_intent,
        )
        output = SpeechSchedulerOutput(
            action=action,
            text_intent=text_intent,
            llm_prompt_basis=llm_prompt_basis(scheduler_input, text_intent),
            reason=reason,
            score=score,
            score_breakdown=score_breakdown,
            trace_id=scheduler_input.trace_id,
        )
        self._log(output)
        _console_event(
            "scheduler_decision",
            action=output.action.value,
            intent=output.text_intent.value,
            score=round(output.score, 4),
            reason=output.reason,
        )
        return output

    def _text_intent(
        self,
        scheduler_input: SpeechSchedulerInput,
        pressure: SpeechPressureState,
        stop_intent: float,
    ) -> SpeechTextIntent:
        if stop_intent >= self.thresholds.stop_threshold:
            return SpeechTextIntent.STOP
        if pressure.calendar_pressure >= max(pressure.reply_pressure, pressure.initiative_pressure):
            if pressure.calendar_pressure > 0:
                return SpeechTextIntent.CALENDAR_NOTICE
        if pressure.initiative_pressure > pressure.reply_pressure:
            return SpeechTextIntent.INITIATIVE
        if _input_text(scheduler_input).startswith(("いや", "ただ", "でも", "というか")):
            return SpeechTextIntent.CORRECTION
        return SpeechTextIntent.REPLY

    def _select_action(
        self,
        scheduler_input: SpeechSchedulerInput,
        score: float,
        interruption_penalty: float,
        stop_intent: float,
        text_intent: SpeechTextIntent,
    ) -> tuple[SpeechSchedulerAction, str]:
        if stop_intent >= self.thresholds.stop_threshold:
            return SpeechSchedulerAction.STOP, "stop intent crossed threshold"
        if (
            interruption_penalty >= self.thresholds.interruption_suppress_threshold
            and score < self.thresholds.speak_threshold
        ):
            return SpeechSchedulerAction.SUPPRESS, "interruption penalty is too high"
        if (
            scheduler_input.partial_stt_text
            and scheduler_input.semantic_saturation
            < self.thresholds.partial_start_saturation_threshold
            and score < self.thresholds.partial_start_score_threshold
        ):
            return SpeechSchedulerAction.SUPPRESS, (
                "partial semantic saturation and score are below start thresholds"
            )
        if scheduler_input.current_speech_order is not None:
            if score > scheduler_input.current_speech_score + self.thresholds.replace_margin:
                return SpeechSchedulerAction.REPLACE_CURRENT, (
                    "new score beat current speech score by replace margin"
                )
            if score > self.thresholds.append_threshold:
                if text_intent == SpeechTextIntent.CALENDAR_NOTICE:
                    return SpeechSchedulerAction.APPEND_AFTER_CURRENT, (
                        "calendar pressure is high enough to append after current reply"
                    )
                return SpeechSchedulerAction.APPEND_AFTER_CURRENT, (
                    "score is high enough to append after current speech"
                )
            return SpeechSchedulerAction.SUPPRESS, "current speech remains stronger"
        if score > self.thresholds.speak_threshold:
            return SpeechSchedulerAction.REPLACE_CURRENT, "reply pressure crossed threshold"
        return SpeechSchedulerAction.SUPPRESS, "score is below speak threshold"

    def _log(self, output: SpeechSchedulerOutput) -> None:
        if self.logger is None:
            return
        self.logger.log(
            "speech_scheduler_decision",
            action=output.action.value,
            text_intent=output.text_intent.value,
            score=output.score,
            score_breakdown=output.score_breakdown,
            reason=output.reason,
            output_id=str(output.id),
        )


def decayed_pressure(state: SpeechPressureState, tau_sec: float) -> SpeechPressureState:
    now = datetime.now(UTC)
    last = state.last_user_spoke_at or state.last_spoke_at
    if last is None or tau_sec <= 0:
        factor = 1.0
    else:
        elapsed = max(0.0, (now - last).total_seconds())
        factor = math.exp(-elapsed / tau_sec)
    return SpeechPressureState(
        reply_pressure=state.reply_pressure * factor,
        initiative_pressure=state.initiative_pressure * factor,
        calendar_pressure=state.calendar_pressure * factor,
        curiosity_pressure=state.curiosity_pressure * factor,
        followup_pressure=state.followup_pressure * factor,
        interruption_penalty=state.interruption_penalty * factor,
        recent_rejection_penalty=state.recent_rejection_penalty * factor,
        fatigue=state.fatigue * factor,
        last_spoke_at=state.last_spoke_at,
        last_user_spoke_at=state.last_user_spoke_at,
    )


def detect_stop_intent(text: str) -> float:
    lowered = text.lower()
    return 1.0 if any(cue in lowered for cue in STOP_CUES) else 0.0


def llm_prompt_basis(
    scheduler_input: SpeechSchedulerInput,
    text_intent: SpeechTextIntent,
) -> str:
    text = _input_text(scheduler_input)
    if text_intent == SpeechTextIntent.CALENDAR_NOTICE:
        return f"calendar_notice urgency={scheduler_input.calendar_urgency:.2f}"
    if text_intent == SpeechTextIntent.INITIATIVE:
        return f"initiative candidate_pressure={scheduler_input.candidate_pressure:.2f}"
    if text_intent == SpeechTextIntent.STOP:
        return "stop intent"
    return f"user_reply: {text}"


def _input_text(scheduler_input: SpeechSchedulerInput) -> str:
    return (
        scheduler_input.final_stt_text
        or scheduler_input.stable_prefix
        or scheduler_input.partial_stt_text
    )


def _console_event(event: str, **fields: object) -> None:
    parts = [f"[tomoko:scheduler] {event}"]
    for key, value in fields.items():
        text = str(value)
        if len(text) > 120:
            text = text[:117] + "..."
        parts.append(f"{key}={text!r}")
    print(" ".join(parts), flush=True)
