from __future__ import annotations

from dataclasses import dataclass, field

from server.shared.logging import JsonlLogger
from server.shared.models import (
    LlmFireDecision,
    LlmFireGateInput,
    LlmFireGateOutput,
    SpeechEmissionDecision,
    SpeechEmissionGateInput,
    SpeechEmissionGateOutput,
)


@dataclass(frozen=True, slots=True)
class LlmFireGateThresholds:
    fire_threshold: float = 0.62
    cancel_threshold: float = 0.8


@dataclass(frozen=True, slots=True)
class SpeechEmissionGateThresholds:
    emit_threshold: float = 0.6
    replace_margin: float = 0.25
    append_threshold: float = 0.62
    stop_threshold: float = 0.8
    hold_interruption_threshold: float = 0.45


@dataclass(slots=True)
class LlmFireGate:
    thresholds: LlmFireGateThresholds = field(default_factory=LlmFireGateThresholds)
    logger: JsonlLogger | None = None

    def decide(self, gate_input: LlmFireGateInput) -> LlmFireGateOutput:
        dialogue = gate_input.dialogue_pressure
        natural = gate_input.natural_speech_pressure
        motivation = gate_input.motivation_pressure
        world = gate_input.world_pressure
        natural_desire = max(
            natural.backchannel_desire,
            natural.light_reaction_desire,
            natural.filler_desire,
            natural.clarification_desire,
        )
        score_breakdown = {
            "dialogue_reply_readiness": dialogue.reply_readiness * 0.55,
            "dialogue_turn_opportunity": dialogue.turn_opportunity * 0.2,
            "natural_speech": natural_desire * 0.18,
            "motivation_initiative": motivation.initiative_desire * 0.25,
            "motivation_personality": motivation.personality_push * 0.12,
            "world_importance": world.importance * 0.28,
            "world_urgency": world.urgency * 0.3,
            "world_deliverability": world.deliverability * 0.18,
            "interruption_risk": -dialogue.interruption_risk * 0.35,
            "restraint": -motivation.restraint * 0.12,
        }
        weighted_score = sum(score_breakdown.values())
        score = max(
            weighted_score,
            dialogue.reply_readiness,
            natural_desire,
            motivation.initiative_desire,
            min(1.0, world.importance * 0.7 + world.deliverability * 0.3),
            world.urgency,
        )
        decision, reason = self._select_decision(gate_input, score=score)
        output = LlmFireGateOutput(
            decision=decision,
            reason=reason,
            score=score,
            score_breakdown=score_breakdown,
            trace_id=gate_input.trace_id,
        )
        self._log(output)
        return output

    def _select_decision(
        self,
        gate_input: LlmFireGateInput,
        *,
        score: float,
    ) -> tuple[LlmFireDecision, str]:
        if gate_input.pending_inference and score >= self.thresholds.cancel_threshold:
            return (
                LlmFireDecision.CANCEL_OR_REPLACE_PENDING,
                "pressure synthesis should replace pending LLM work",
            )
        if score >= self.thresholds.fire_threshold:
            return LlmFireDecision.FIRE, "pressure synthesis crossed LLM fire threshold"
        return LlmFireDecision.DO_NOT_FIRE, "pressure synthesis is below LLM fire threshold"

    def _log(self, output: LlmFireGateOutput) -> None:
        if self.logger is None:
            return
        self.logger.log(
            "llm_fire_gate",
            decision=output.decision.value,
            score=output.score,
            score_breakdown=output.score_breakdown,
            reason=output.reason,
            output_id=str(output.id),
        )


@dataclass(slots=True)
class SpeechEmissionGate:
    thresholds: SpeechEmissionGateThresholds = field(
        default_factory=SpeechEmissionGateThresholds
    )
    logger: JsonlLogger | None = None

    def decide(self, gate_input: SpeechEmissionGateInput) -> SpeechEmissionGateOutput:
        dialogue = gate_input.dialogue_pressure
        natural = gate_input.natural_speech_pressure
        motivation = gate_input.motivation_pressure
        world = gate_input.world_pressure
        candidate = gate_input.candidate
        yielding = gate_input.turn_materials.p_yielding or 0.0
        material_interruption_risk = (
            gate_input.turn_materials.speech_probability * (1.0 - yielding)
            if gate_input.turn_materials.user_speaking
            else 0.0
        )
        interruption_risk = max(dialogue.interruption_risk, material_interruption_risk)
        score_breakdown = {
            "candidate_priority": candidate.priority * 0.5,
            "candidate_freshness": candidate.freshness * 0.2,
            "candidate_confidence": candidate.semantic_confidence * 0.25,
            "dialogue_opportunity": dialogue.turn_opportunity * 0.22,
            "naturalness": natural.naturalness * 0.12,
            "motivation": motivation.initiative_desire * 0.25,
            "world_urgency": world.urgency * 0.18,
            "world_deliverability": world.deliverability * 0.18,
            "interruption_risk": -interruption_risk * 0.75,
            "misunderstanding_risk": -candidate.misunderstanding_risk * 0.45,
            "recent_rejection": -gate_input.recent_rejection_penalty * 0.5,
            "fatigue": -gate_input.fatigue * 0.35,
            "restraint": -motivation.restraint * 0.12,
        }
        score = sum(score_breakdown.values())
        decision, reason = self._select_decision(gate_input, score, interruption_risk)
        output = SpeechEmissionGateOutput(
            decision=decision,
            reason=reason,
            score=score,
            score_breakdown=score_breakdown,
            trace_id=gate_input.trace_id,
        )
        self._log(output)
        return output

    def _select_decision(
        self,
        gate_input: SpeechEmissionGateInput,
        score: float,
        interruption_risk: float,
    ) -> tuple[SpeechEmissionDecision, str]:
        if gate_input.stop_intent >= self.thresholds.stop_threshold:
            return SpeechEmissionDecision.STOP, "stop intent crossed emission threshold"
        if (
            interruption_risk >= self.thresholds.hold_interruption_threshold
            and score < self.thresholds.emit_threshold
        ):
            return SpeechEmissionDecision.HOLD, "user speech interruption risk is too high"
        if gate_input.current_speech_order is not None:
            if score > gate_input.current_speech_score + self.thresholds.replace_margin:
                return (
                    SpeechEmissionDecision.REPLACE_CURRENT,
                    "prepared speech beat current speech by replace margin",
                )
            if score >= self.thresholds.append_threshold:
                return SpeechEmissionDecision.APPEND_AFTER_CURRENT, (
                    "prepared speech is high enough to append"
                )
            return SpeechEmissionDecision.HOLD, "current speech remains stronger"
        if score >= self.thresholds.emit_threshold:
            return SpeechEmissionDecision.EMIT_NOW, "prepared speech crossed emit threshold"
        return SpeechEmissionDecision.SUPPRESS, "emission score is below threshold"

    def _log(self, output: SpeechEmissionGateOutput) -> None:
        if self.logger is None:
            return
        self.logger.log(
            "speech_emission_gate",
            decision=output.decision.value,
            score=output.score,
            score_breakdown=output.score_breakdown,
            reason=output.reason,
            output_id=str(output.id),
        )
