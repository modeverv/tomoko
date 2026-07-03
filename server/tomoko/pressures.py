from __future__ import annotations

from dataclasses import dataclass

from server.shared.models import (
    ConversationHistoryItem,
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
            yielding_opportunity=yielding,
            silence_opportunity=silence_opportunity,
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
        recent_history: list[ConversationHistoryItem] | None = None,
        current_text: str = "",
    ) -> MotivationPressure:
        silence_opportunity = min(1.0, turn_materials.silence_ms / 8000.0)
        talkativeness = _clamp(personality_materials.talkativeness)
        curiosity = _clamp(personality_materials.curiosity)
        restraint = _clamp(personality_materials.restraint)
        interrupt_tolerance = _clamp(personality_materials.interrupt_tolerance)
        conversation_heat = _conversation_heat(recent_history or [])
        topic_continuity = _topic_continuity(recent_history or [], current_text)
        personality_drive = (
            talkativeness * 0.42
            + curiosity * 0.28
            + conversation_heat * 0.22
            + topic_continuity * 0.20
        )
        speech_drag = turn_materials.speech_probability * (0.22 - interrupt_tolerance * 0.12)
        initiative_desire = _clamp(
            silence_opportunity * (talkativeness * 0.45 + curiosity * 0.2)
            + personality_drive * 0.35
            - speech_drag
            - restraint * 0.22
        )
        threshold_shift = min(
            0.22,
            _clamp(
                initiative_desire * 0.16
                + interrupt_tolerance * 0.06
                + conversation_heat * 0.04
                + topic_continuity * 0.04
                - restraint * 0.06
            ),
        )
        return MotivationPressure(
            initiative_desire=initiative_desire,
            personality_push=_clamp(talkativeness * 0.6 + curiosity * 0.4),
            conversation_heat=conversation_heat,
            topic_continuity=topic_continuity,
            threshold_shift=threshold_shift,
            restraint=restraint,
            interrupt_tolerance=interrupt_tolerance,
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
        user_presence = 1.0 if world_materials.user_present else 0.0
        raw_importance = _clamp(
            max(
                world_materials.external_result_importance,
                world_materials.calendar_urgency,
                world_materials.candidate_pressure,
                world_materials.followup_importance,
                world_materials.memory_relevance,
                world_materials.curiosity_relevance * personality_materials.curiosity,
            )
        )
        raw_urgency = _clamp(
            world_materials.calendar_urgency * 0.6
            + world_materials.external_result_importance * 0.3
            + world_materials.candidate_pressure * 0.25
            + world_materials.followup_importance * 0.2
        )
        yielding = _clamp(turn_materials.p_yielding or 0.0)
        silence_opportunity = min(1.0, turn_materials.silence_ms / 2200.0)
        raw_deliverability = _clamp(
            max(yielding, silence_opportunity)
            - turn_materials.speech_probability * 0.35
            - personality_materials.restraint * 0.1
        )
        decay = _clamp(world_materials.followup_age_ms / 3_600_000)
        reason = (
            "user absence suppresses world pressure delivery"
            if user_presence == 0.0
            else "world materials converted to world pressure"
        )
        return WorldPressure(
            importance=raw_importance * user_presence,
            urgency=raw_urgency * user_presence,
            relevance=_clamp(
                max(
                    world_materials.memory_relevance,
                    world_materials.curiosity_relevance,
                    world_materials.candidate_pressure,
                )
            ),
            deliverability=raw_deliverability * user_presence,
            decay=decay,
            candidate_pressure=_clamp(world_materials.candidate_pressure) * user_presence,
            user_presence=user_presence,
            user_absence=1.0 - user_presence,
            reason=reason,
            trace_id=turn_materials.trace_id,
        )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _conversation_heat(history: list[ConversationHistoryItem]) -> float:
    if not history:
        return 0.0
    recent = history[-6:]
    return min(1.0, len(recent) / 4.0)


def _topic_continuity(
    history: list[ConversationHistoryItem],
    current_text: str,
) -> float:
    current = _topic_chars(current_text)
    if not current:
        return 0.0
    recent_text = "".join(item.text for item in history[-6:])
    previous = _topic_chars(recent_text)
    if not previous:
        return 0.0
    return len(current & previous) / len(current | previous)


def _topic_chars(text: str) -> set[str]:
    ignored = set(" 　、。！？!?.,:;「」『』（）()[]【】\n\t")
    return {char for char in text if char not in ignored}
