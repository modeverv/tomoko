from __future__ import annotations

from dataclasses import dataclass

from server.shared.models import (
    DialogueTurnPressure,
    MotivationPressure,
    NaturalSpeechPressure,
    PersonalityMaterials,
    TurnMaterials,
    WorldMaterials,
    WorldPressure,
)


@dataclass(frozen=True, slots=True)
class DialogueTurnPressureModel:
    def calculate(
        self,
        *,
        turn_materials: TurnMaterials,
        semantic_saturation: float,
        stable_prefix: str = "",
        final_stt_text: str = "",
    ) -> DialogueTurnPressure:
        text = final_stt_text or stable_prefix or turn_materials.stt_partial
        yielding = _clamp(turn_materials.p_yielding or 0.0)
        silence_opportunity = min(1.0, turn_materials.silence_ms / 1200.0)
        text_presence = 1.0 if text else 0.0
        final_text_bonus = 1.0 if final_stt_text else 0.0
        interruption_risk = (
            turn_materials.speech_probability * (1.0 - yielding)
            if turn_materials.user_speaking
            else 0.0
        )
        turn_opportunity = _clamp(max(yielding, silence_opportunity) - interruption_risk * 0.4)
        reply_readiness = _clamp(
            semantic_saturation * 0.62
            + text_presence * 0.18
            + final_text_bonus * 0.15
            + turn_opportunity * 0.25
            - interruption_risk * 0.25
        )
        return DialogueTurnPressure(
            reply_readiness=reply_readiness,
            turn_opportunity=turn_opportunity,
            interruption_risk=_clamp(interruption_risk),
            semantic_saturation=_clamp(semantic_saturation),
            text_presence=text_presence,
            final_text_bonus=final_text_bonus,
            reason="dialogue materials converted to turn pressure",
            trace_id=turn_materials.trace_id,
        )


@dataclass(frozen=True, slots=True)
class NaturalSpeechPressureModel:
    def calculate(
        self,
        *,
        turn_materials: TurnMaterials,
        personality_materials: PersonalityMaterials,
    ) -> NaturalSpeechPressure:
        yielding = _clamp(turn_materials.p_yielding or 0.0)
        react = _clamp(turn_materials.p_bc_react or 0.0)
        emo = _clamp(turn_materials.p_bc_emo or 0.0)
        silence_opportunity = min(1.0, turn_materials.silence_ms / 1800.0)
        restraint = _clamp(personality_materials.restraint)
        naturalness = _clamp(max(yielding, silence_opportunity) * (1.0 - restraint * 0.35))
        return NaturalSpeechPressure(
            backchannel_desire=_clamp(max(react, emo) * naturalness),
            light_reaction_desire=_clamp((react * 0.7 + emo * 0.3) * naturalness),
            filler_desire=_clamp(silence_opportunity * personality_materials.empathy),
            clarification_desire=0.0,
            naturalness=naturalness,
            reason="maai and turn materials converted to natural speech pressure",
            trace_id=turn_materials.trace_id,
        )


@dataclass(frozen=True, slots=True)
class MotivationPressureModel:
    def calculate(
        self,
        *,
        turn_materials: TurnMaterials,
        personality_materials: PersonalityMaterials,
    ) -> MotivationPressure:
        silence_opportunity = min(1.0, turn_materials.silence_ms / 8000.0)
        talkativeness = _clamp(personality_materials.talkativeness)
        curiosity = _clamp(personality_materials.curiosity)
        restraint = _clamp(personality_materials.restraint)
        initiative_desire = _clamp(
            silence_opportunity * (talkativeness * 0.55 + curiosity * 0.25)
            - turn_materials.speech_probability * 0.25
            - restraint * 0.2
        )
        return MotivationPressure(
            initiative_desire=initiative_desire,
            personality_push=_clamp(talkativeness * 0.6 + curiosity * 0.4),
            restraint=restraint,
            interrupt_tolerance=_clamp(personality_materials.interrupt_tolerance),
            reason="personality materials converted to motivation pressure",
            trace_id=turn_materials.trace_id,
        )


@dataclass(frozen=True, slots=True)
class WorldPressureModel:
    def calculate(
        self,
        *,
        turn_materials: TurnMaterials,
        world_materials: WorldMaterials,
        personality_materials: PersonalityMaterials,
    ) -> WorldPressure:
        importance = _clamp(
            max(
                world_materials.external_result_importance,
                world_materials.calendar_urgency,
                world_materials.followup_importance,
                world_materials.memory_relevance,
                world_materials.curiosity_relevance * personality_materials.curiosity,
            )
        )
        urgency = _clamp(
            world_materials.calendar_urgency * 0.6
            + world_materials.external_result_importance * 0.3
            + world_materials.followup_importance * 0.2
        )
        yielding = _clamp(turn_materials.p_yielding or 0.0)
        silence_opportunity = min(1.0, turn_materials.silence_ms / 2200.0)
        deliverability = _clamp(
            max(yielding, silence_opportunity)
            - turn_materials.speech_probability * 0.35
            - personality_materials.restraint * 0.1
        )
        decay = _clamp(world_materials.followup_age_ms / 3_600_000)
        return WorldPressure(
            importance=importance,
            urgency=urgency,
            relevance=_clamp(
                max(world_materials.memory_relevance, world_materials.curiosity_relevance)
            ),
            deliverability=deliverability,
            decay=decay,
            reason="world materials converted to world pressure",
            trace_id=turn_materials.trace_id,
        )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
