from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from server.llm.chat import ChatBackend, create_default_real_chat_backend
from server.shared.models import (
    ContextSnapshot,
    ConversationHistoryItem,
    DialogueTurnPressure,
    DurableUtterance,
    LlmFireDecision,
    LlmFireGateInput,
    ModelOutputEvent,
    MotivationPressure,
    NaturalSpeechPressure,
    PartialTranscriptObservation,
    PersonalityMaterials,
    PreparedSpeechCandidate,
    PromptRequest,
    SemanticSaturationResult,
    SpeechEmissionDecision,
    SpeechEmissionGateInput,
    SpeechOrder,
    SpeechOrderMode,
    SpeechSchedulerAction,
    SpeechSchedulerInput,
    SpeechSchedulerOutput,
    SpeechTextIntent,
    TurnMaterials,
    WorldMaterials,
    WorldPressure,
)
from server.tomoko.append_dedupe import (
    AppendDedupeGuard,
    create_default_append_dedupe_guard,
    decision_score_breakdown,
)
from server.tomoko.context import ContextSnapshotBuilderV2
from server.tomoko.gates import LlmFireGate, SpeechEmissionGate
from server.tomoko.main import TomokoProcessCore
from server.tomoko.pressures import (
    DialogueTurnPressureModel,
    MotivationPressureModel,
    NaturalSpeechPressureModel,
    WorldPressureModel,
)
from server.tomoko.prompt import PromptBuilderV2
from server.tomoko.scheduler import SpeechScheduler, detect_stop_intent
from server.tomoko.semantic import (
    SemanticSaturationJudge,
    create_default_saturation_judge,
)
from server.tomoko.session import SessionBoundaryModel

PARTIAL_CONFIRM_SATURATION_THRESHOLD = 0.75
PARTIAL_CONFIRM_REQUIRED_COUNT = 2


@dataclass(slots=True)
class TomokoConversationResult:
    observation: PartialTranscriptObservation
    durable_utterance: DurableUtterance | None
    saturation: SemanticSaturationResult
    scheduler_output: SpeechSchedulerOutput
    context_snapshot: ContextSnapshot | None
    prompt_request: PromptRequest | None
    speech_order: SpeechOrder | None
    model_events: list[ModelOutputEvent] = field(default_factory=list)


@dataclass(slots=True)
class TomokoConversationCore:
    session_model: SessionBoundaryModel
    saturation_judge: SemanticSaturationJudge
    scheduler: SpeechScheduler
    chat_backend: ChatBackend
    llm_fire_gate: LlmFireGate = field(default_factory=LlmFireGate)
    speech_emission_gate: SpeechEmissionGate = field(default_factory=SpeechEmissionGate)
    dialogue_pressure_model: DialogueTurnPressureModel = field(
        default_factory=DialogueTurnPressureModel
    )
    natural_speech_pressure_model: NaturalSpeechPressureModel = field(
        default_factory=NaturalSpeechPressureModel
    )
    motivation_pressure_model: MotivationPressureModel = field(
        default_factory=MotivationPressureModel
    )
    world_pressure_model: WorldPressureModel = field(default_factory=WorldPressureModel)
    personality_materials: PersonalityMaterials = field(default_factory=PersonalityMaterials)
    world_materials: WorldMaterials = field(default_factory=WorldMaterials)
    prompt_builder: PromptBuilderV2 = field(default_factory=PromptBuilderV2)
    context_builder: ContextSnapshotBuilderV2 = field(default_factory=ContextSnapshotBuilderV2)
    append_dedupe_guard: AppendDedupeGuard | None = None
    tomoko_core: TomokoProcessCore | None = None
    turn_materials: TurnMaterials | None = None
    current_speech_order: SpeechOrder | None = None
    current_speech_score: float = 0.0
    _recent_utterances: list[str] = field(default_factory=list)
    _recent_history: list[ConversationHistoryItem] = field(default_factory=list)
    _partial_history: list[str] = field(default_factory=list)
    _active_partial_order: SpeechOrder | None = None
    _active_partial_basis_text: str = ""
    _last_reconciled_final_text: str = ""
    _partial_start_confirm_text: str = ""
    _partial_start_confirm_count: int = 0
    _partial_start_gate_last_reason: str = ""
    _last_final_user_text: str = ""
    _last_final_user_audio_ended_at: datetime | None = None

    def update_turn_materials(self, materials: TurnMaterials) -> None:
        self.turn_materials = materials

    async def handle_observation(
        self,
        observation: PartialTranscriptObservation,
        *,
        session_id_override: UUID | None = None,
        prior_session_history: list[ConversationHistoryItem] | None = None,
    ) -> TomokoConversationResult:
        text = observation.text.strip()
        core = self.tomoko_core or TomokoProcessCore(self.session_model)
        durable = (
            core.adopt_final_observation(
                observation,
                session_id_override=session_id_override,
            )
            if observation.is_final
            else None
        )
        if observation.is_final and durable is None:
            return self._blocked_result(observation, text, core)

        if durable is not None:
            basis_text = durable.text
            session_id = durable.session_id
        else:
            self._partial_history.append(text)
            basis_text = text
            session_id = None

        if self._should_reconcile_observation(observation, basis_text):
            reconcile_reason = self._reconcile_reason(observation, basis_text)
            saturation = SemanticSaturationResult(
                saturation=1.0 if observation.is_final else 0.0,
                source="reconciled_final" if observation.is_final else "reconciled_partial",
                basis_text=basis_text,
                trace_id=observation.trace_id,
            )
            scheduler_output = self.scheduler.decide(
                SpeechSchedulerInput(
                    partial_stt_text="" if observation.is_final else basis_text,
                    final_stt_text=basis_text if observation.is_final else "",
                    semantic_saturation=0.0,
                    trace_id=observation.trace_id,
                )
            )
            scheduler_output.reason = reconcile_reason
            if durable is not None and prior_session_history is None:
                self._recent_utterances.append(durable.text)
                self._recent_history.append(
                    ConversationHistoryItem(speaker="user", text=durable.text)
                )
                self._active_partial_order = None
                self._active_partial_basis_text = ""
                self._last_reconciled_final_text = durable.text
                self.current_speech_order = None
                self.current_speech_score = 0.0
            if durable is not None:
                self._remember_final_user(durable.text, observation)
            snapshot = self.context_builder.build(
                session_id=session_id,
                recent_utterances=self._recent_utterances[-8:],
                summaries=[],
                calendar_loader=lambda: {},
                user_status=None,
                candidates=[],
                recent_history=self._recent_history[-8:],
            )
            return TomokoConversationResult(
                observation=observation,
                durable_utterance=durable,
                saturation=saturation,
                scheduler_output=scheduler_output,
                context_snapshot=snapshot,
                prompt_request=None,
                speech_order=None,
            )

        saturation = await self.saturation_judge.judge(
            basis_text,
            partial=not observation.is_final,
        )
        turn_materials = _turn_materials_for_observation(
            self.turn_materials,
            observation=observation,
            basis_text=basis_text,
        )
        stable_prefix = basis_text if observation.is_final else _stable_partial(
            self._partial_history
        )
        dialogue_pressure = self.dialogue_pressure_model.calculate(
            turn_materials=turn_materials,
            semantic_saturation=saturation.saturation,
            stable_prefix=stable_prefix,
            final_stt_text=basis_text if observation.is_final else "",
        )
        natural_pressure = self.natural_speech_pressure_model.calculate(
            turn_materials=turn_materials,
            personality_materials=self.personality_materials,
        )
        motivation_pressure = self.motivation_pressure_model.calculate(
            turn_materials=turn_materials,
            personality_materials=self.personality_materials,
        )
        world_pressure = self.world_pressure_model.calculate(
            turn_materials=turn_materials,
            world_materials=self.world_materials,
            personality_materials=self.personality_materials,
        )
        llm_fire = self.llm_fire_gate.decide(
            LlmFireGateInput(
                turn_materials=turn_materials,
                dialogue_pressure=dialogue_pressure,
                natural_speech_pressure=natural_pressure,
                motivation_pressure=motivation_pressure,
                world_pressure=world_pressure,
                trace_id=observation.trace_id,
            )
        )
        scheduler_output = _scheduler_output_from_gate(
            action=_action_for_llm_fire_decision(llm_fire.decision),
            text_intent=SpeechTextIntent.REPLY,
            basis_text=basis_text,
            reason=llm_fire.reason,
            score=llm_fire.score,
            score_breakdown={
                **llm_fire.score_breakdown,
                **_pressure_breakdown(
                    dialogue_pressure,
                    natural_pressure,
                    motivation_pressure,
                    world_pressure,
                ),
            },
            trace_id=observation.trace_id,
        )
        prompt_history = (
            prior_session_history
            if prior_session_history is not None
            else self._recent_history[-8:]
        )
        snapshot = self.context_builder.build(
            session_id=session_id,
            recent_utterances=self._recent_utterances[-8:],
            summaries=[],
            calendar_loader=lambda: {},
            user_status=None,
            candidates=[],
            recent_history=prompt_history,
        )

        if detect_stop_intent(basis_text) >= 0.8:
            scheduler_output = _scheduler_output_from_gate(
                action=SpeechSchedulerAction.STOP,
                text_intent=SpeechTextIntent.STOP,
                basis_text=basis_text,
                reason="stop intent crossed emission threshold",
                score=1.0,
                score_breakdown={"stop_intent": 1.0},
                trace_id=observation.trace_id,
            )

        if (
            observation.is_final
            and durable is not None
            and scheduler_output.action
            not in (SpeechSchedulerAction.SUPPRESS, SpeechSchedulerAction.STOP)
        ):
            dedupe_decision = self._inspect_append_dedupe(
                current_text=basis_text,
                observation=observation,
            )
            if dedupe_decision is not None:
                scheduler_output.score_breakdown = {
                    **scheduler_output.score_breakdown,
                    **decision_score_breakdown(dedupe_decision),
                }
                if dedupe_decision.should_suppress:
                    scheduler_output.action = SpeechSchedulerAction.SUPPRESS
                    scheduler_output.reason = dedupe_decision.reason
                    scheduler_output.score = 0.0
                    _console_event(
                        "append_dedupe_suppressed",
                        previous=dedupe_decision.previous_user_text,
                        current=dedupe_decision.current_user_text,
                        duplicate_score=round(dedupe_decision.duplicate_score, 4),
                        continuation_score=round(dedupe_decision.continuation_score, 4),
                        new_intent_score=round(dedupe_decision.new_intent_score, 4),
                    )
                    return TomokoConversationResult(
                        observation=observation,
                        durable_utterance=durable,
                        saturation=saturation,
                        scheduler_output=scheduler_output,
                        context_snapshot=snapshot,
                        prompt_request=None,
                        speech_order=None,
                    )

        if (
            not observation.is_final
            and scheduler_output.action
            not in (SpeechSchedulerAction.SUPPRESS, SpeechSchedulerAction.STOP)
            and not self._partial_start_gate_allows(
                basis_text,
                saturation=saturation.saturation,
                score=scheduler_output.score,
            )
        ):
            scheduler_output.action = SpeechSchedulerAction.SUPPRESS
            scheduler_output.reason = self._partial_start_gate_last_reason
            return TomokoConversationResult(
                observation=observation,
                durable_utterance=durable,
                saturation=saturation,
                scheduler_output=scheduler_output,
                context_snapshot=snapshot,
                prompt_request=None,
                speech_order=None,
            )

        if scheduler_output.action == SpeechSchedulerAction.STOP:
            if durable is not None and prior_session_history is None:
                self._recent_utterances.append(durable.text)
                self._recent_history.append(
                    ConversationHistoryItem(speaker="user", text=durable.text)
                )
            if durable is not None:
                self._remember_final_user(durable.text, observation)
            order = SpeechOrder(
                text="",
                mode=SpeechOrderMode.STOP,
                reason=scheduler_output.reason,
                priority=100,
                scheduler_decision_id=scheduler_output.id,
                trace_id=observation.trace_id,
            )
            self.current_speech_order = None
            self.current_speech_score = 0.0
            return TomokoConversationResult(
                observation=observation,
                durable_utterance=durable,
                saturation=saturation,
                scheduler_output=scheduler_output,
                context_snapshot=snapshot,
                prompt_request=None,
                speech_order=order,
            )

        if scheduler_output.action == SpeechSchedulerAction.SUPPRESS:
            if durable is not None and prior_session_history is None:
                self._recent_utterances.append(durable.text)
                self._recent_history.append(
                    ConversationHistoryItem(speaker="user", text=durable.text)
                )
            if durable is not None:
                self._remember_final_user(durable.text, observation)
            return TomokoConversationResult(
                observation=observation,
                durable_utterance=durable,
                saturation=saturation,
                scheduler_output=scheduler_output,
                context_snapshot=snapshot,
                prompt_request=None,
                speech_order=None,
            )

        request = self.prompt_builder.build_main_reply(
            snapshot,
            basis_text,
            concise=not observation.is_final,
        )
        model_events = await self._generate_model_events(request)
        text_out = next(
            (event.text for event in model_events if event.event_kind == "complete"),
            "",
        ).strip()
        candidate = PreparedSpeechCandidate(
            text=text_out,
            priority=max(0.0, min(1.0, scheduler_output.score)),
            freshness=1.0,
            semantic_confidence=saturation.saturation,
            reason=llm_fire.reason,
            trace_id=observation.trace_id,
        )
        emission = self.speech_emission_gate.decide(
            SpeechEmissionGateInput(
                candidate=candidate,
                turn_materials=turn_materials,
                dialogue_pressure=dialogue_pressure,
                natural_speech_pressure=natural_pressure,
                motivation_pressure=motivation_pressure,
                world_pressure=world_pressure,
                current_speech_order=self.current_speech_order,
                current_speech_score=self.current_speech_score,
                tomoko_currently_speaking=self.current_speech_order is not None,
                stop_intent=detect_stop_intent(basis_text),
                trace_id=observation.trace_id,
            )
        )
        scheduler_output.action = _action_for_emission_decision(emission.decision)
        scheduler_output.reason = emission.reason
        scheduler_output.score = emission.score
        scheduler_output.score_breakdown = {
            **scheduler_output.score_breakdown,
            **{f"emission_{key}": value for key, value in emission.score_breakdown.items()},
        }
        if scheduler_output.action == SpeechSchedulerAction.SUPPRESS:
            if durable is not None and prior_session_history is None:
                self._recent_utterances.append(durable.text)
                self._recent_history.append(
                    ConversationHistoryItem(speaker="user", text=durable.text)
                )
            if durable is not None:
                self._remember_final_user(durable.text, observation)
            return TomokoConversationResult(
                observation=observation,
                durable_utterance=durable,
                saturation=saturation,
                scheduler_output=scheduler_output,
                context_snapshot=snapshot,
                prompt_request=request,
                speech_order=None,
                model_events=model_events,
            )
        order = SpeechOrder(
            text=text_out,
            mode=_order_mode_for_action(scheduler_output.action),
            reason=scheduler_output.reason,
            priority=_priority_for_output(scheduler_output),
            scheduler_decision_id=scheduler_output.id,
            trace_id=observation.trace_id,
        )
        self.current_speech_order = order
        self.current_speech_score = scheduler_output.score
        if not observation.is_final:
            self._active_partial_order = order
            self._active_partial_basis_text = basis_text
            self._partial_start_confirm_text = basis_text
            self._partial_start_confirm_count = 0
        if durable is not None and prior_session_history is None:
            self._recent_utterances.append(durable.text)
            self._recent_history.append(
                ConversationHistoryItem(speaker="user", text=durable.text)
            )
        if durable is not None:
            self._remember_final_user(durable.text, observation)
        if text_out:
            if prior_session_history is None:
                self._recent_history.append(
                    ConversationHistoryItem(speaker="tomoko", text=text_out)
                )
        _console_event(
            "speech_order_created",
            order_id=str(order.id),
            mode=order.mode.value,
            chars=len(order.text),
        )
        return TomokoConversationResult(
            observation=observation,
            durable_utterance=durable,
            saturation=saturation,
            scheduler_output=scheduler_output,
            context_snapshot=snapshot,
            prompt_request=request,
            speech_order=order,
            model_events=model_events,
        )

    def _should_reconcile_observation(
        self,
        observation: PartialTranscriptObservation,
        basis_text: str,
    ) -> bool:
        return bool(self._reconcile_reason(observation, basis_text))

    def _reconcile_reason(
        self,
        observation: PartialTranscriptObservation,
        basis_text: str,
    ) -> str:
        if self._active_partial_order is None or not self._active_partial_basis_text:
            if (
                not observation.is_final
                and bool(self._last_reconciled_final_text)
                and _similar_enough(basis_text, self._last_reconciled_final_text)
            ):
                return "partial reconciled with active partial reply"
            return ""
        same_trace = observation.trace_id == self._active_partial_order.trace_id
        if observation.is_final:
            if _similar_enough(self._active_partial_basis_text, basis_text):
                return "final reconciled with active partial reply"
            if same_trace:
                return "final discarded after active partial reply in same trace"
            return ""
        if _similar_enough(self._active_partial_basis_text, basis_text):
            return "partial reconciled with active partial reply"
        if same_trace:
            return "partial discarded after active partial reply in same trace"
        return ""

    def _partial_start_gate_allows(
        self,
        basis_text: str,
        *,
        saturation: float,
        score: float,
    ) -> bool:
        if (
            saturation < PARTIAL_CONFIRM_SATURATION_THRESHOLD
            and score < self.scheduler.thresholds.partial_start_score_threshold
        ):
            self._partial_start_confirm_text = ""
            self._partial_start_confirm_count = 0
            self._partial_start_gate_last_reason = (
                "partial start gate is below confirmation thresholds"
            )
            return False
        if not self._partial_start_confirm_text:
            self._partial_start_confirm_text = basis_text
            self._partial_start_confirm_count = 1
            self._partial_start_gate_last_reason = (
                "partial start gate is waiting for confirmation"
            )
            return False
        if not _similar_enough(self._partial_start_confirm_text, basis_text):
            self._partial_start_confirm_text = basis_text
            self._partial_start_confirm_count = 1
            self._partial_start_gate_last_reason = "partial start gate text changed too much"
            return False
        self._partial_start_confirm_text = basis_text
        self._partial_start_confirm_count += 1
        self._partial_start_gate_last_reason = ""
        return self._partial_start_confirm_count >= PARTIAL_CONFIRM_REQUIRED_COUNT

    async def _generate_model_events(self, request: PromptRequest) -> list[ModelOutputEvent]:
        events: list[ModelOutputEvent] = []
        parts: list[str] = []
        async for delta in self.chat_backend.stream(request):
            parts.append(delta)
            events.append(
                ModelOutputEvent(
                    request_id=request.id,
                    event_kind="delta",
                    text_delta=delta,
                    trace_id=request.trace_id,
                )
            )
        full_text = "".join(parts)
        events.append(
            ModelOutputEvent(
                request_id=request.id,
                event_kind="complete",
                text=full_text,
                trace_id=request.trace_id,
            )
        )
        return events

    def _inspect_append_dedupe(
        self,
        *,
        current_text: str,
        observation: PartialTranscriptObservation,
    ):
        if self.append_dedupe_guard is None or not self._last_final_user_text:
            return None
        if self._last_final_user_audio_ended_at is None:
            return None
        time_delta_ms = max(
            0,
            int(
                (
                    observation.audio_ended_at - self._last_final_user_audio_ended_at
                ).total_seconds()
                * 1000
            ),
        )
        return self.append_dedupe_guard.inspect(
            previous_user_text=self._last_final_user_text,
            current_user_text=current_text,
            time_delta_ms=time_delta_ms,
            tomoko_speaking=self.current_speech_order is not None,
            speech_queue_active=self.current_speech_order is not None,
            current_is_final=observation.is_final,
        )

    def _remember_final_user(
        self,
        text: str,
        observation: PartialTranscriptObservation,
    ) -> None:
        if not observation.is_final:
            return
        self._last_final_user_text = text
        self._last_final_user_audio_ended_at = observation.audio_ended_at

    def _blocked_result(
        self,
        observation: PartialTranscriptObservation,
        text: str,
        core: TomokoProcessCore,
    ) -> TomokoConversationResult:
        reason = core.block_reason_for_final_observation(observation) or "blocked"
        saturation = SemanticSaturationResult(
            saturation=0.0,
            source=f"blocked_{reason}",
            basis_text=text,
            trace_id=observation.trace_id,
        )
        scheduler_output = self.scheduler.decide(
            SpeechSchedulerInput(
                final_stt_text=text,
                semantic_saturation=0.0,
                trace_id=observation.trace_id,
            )
        )
        return TomokoConversationResult(
            observation=observation,
            durable_utterance=None,
            saturation=saturation,
            scheduler_output=scheduler_output,
            context_snapshot=None,
            prompt_request=None,
            speech_order=None,
        )


def create_default_conversation_core() -> TomokoConversationCore:
    return TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=create_default_saturation_judge(),
        scheduler=SpeechScheduler(),
        llm_fire_gate=LlmFireGate(),
        speech_emission_gate=SpeechEmissionGate(),
        append_dedupe_guard=create_default_append_dedupe_guard(),
        chat_backend=create_default_real_chat_backend(),
    )


def _turn_materials_for_observation(
    current: TurnMaterials | None,
    *,
    observation: PartialTranscriptObservation,
    basis_text: str,
) -> TurnMaterials:
    if current is None:
        return TurnMaterials(
            window_ms=200,
            user_speaking=not observation.is_final,
            speech_probability=0.5 if not observation.is_final else 0.0,
            p_yielding=observation.p_yielding if observation.p_yielding is not None else 1.0,
            silence_ms=400 if observation.is_final else 0,
            playback_active=False,
            stt_partial=basis_text if not observation.is_final else "",
            trace_id=observation.trace_id,
        )
    return TurnMaterials(
        window_ms=current.window_ms,
        user_speaking=current.user_speaking,
        speech_probability=current.speech_probability,
        p_yielding=current.p_yielding
        if current.p_yielding is not None
        else observation.p_yielding
        if observation.p_yielding is not None
        else 1.0,
        silence_ms=max(current.silence_ms, 400 if observation.is_final else 0),
        playback_active=current.playback_active,
        p_bc_react=current.p_bc_react,
        p_bc_emo=current.p_bc_emo,
        audio_rms=current.audio_rms,
        stt_partial=basis_text if not observation.is_final else current.stt_partial,
        trace_id=observation.trace_id,
    )


def _scheduler_output_from_gate(
    *,
    action: SpeechSchedulerAction,
    text_intent: SpeechTextIntent,
    basis_text: str,
    reason: str,
    score: float,
    score_breakdown: dict[str, float],
    trace_id: UUID,
) -> SpeechSchedulerOutput:
    return SpeechSchedulerOutput(
        action=action,
        text_intent=text_intent,
        llm_prompt_basis=basis_text,
        reason=reason,
        score=score,
        score_breakdown=score_breakdown,
        trace_id=trace_id,
    )


def _action_for_llm_fire_decision(decision: LlmFireDecision) -> SpeechSchedulerAction:
    if decision == LlmFireDecision.DO_NOT_FIRE:
        return SpeechSchedulerAction.SUPPRESS
    return SpeechSchedulerAction.REPLACE_CURRENT


def _pressure_breakdown(
    dialogue: DialogueTurnPressure,
    natural: NaturalSpeechPressure,
    motivation: MotivationPressure,
    world: WorldPressure,
) -> dict[str, float]:
    return {
        "pressure_dialogue_reply_readiness": dialogue.reply_readiness,
        "pressure_dialogue_turn_opportunity": dialogue.turn_opportunity,
        "pressure_dialogue_interruption_risk": dialogue.interruption_risk,
        "pressure_natural_backchannel_desire": natural.backchannel_desire,
        "pressure_natural_light_reaction_desire": natural.light_reaction_desire,
        "pressure_natural_filler_desire": natural.filler_desire,
        "pressure_motivation_initiative_desire": motivation.initiative_desire,
        "pressure_motivation_personality_push": motivation.personality_push,
        "pressure_world_importance": world.importance,
        "pressure_world_urgency": world.urgency,
        "pressure_world_deliverability": world.deliverability,
    }


def _stable_partial(partials: list[str]) -> str:
    if not partials:
        return ""
    prefix = partials[0]
    for partial in partials[1:]:
        while prefix and not partial.startswith(prefix):
            prefix = prefix[:-1]
    return prefix


def _similar_enough(left: str, right: str) -> bool:
    left_normalized = _normalize_for_reconcile(left)
    right_normalized = _normalize_for_reconcile(right)
    if not left_normalized or not right_normalized:
        return False
    return (
        left_normalized in right_normalized
        or right_normalized in left_normalized
        or _prefix_ratio(left_normalized, right_normalized) >= 0.7
    )


def _normalize_for_reconcile(text: str) -> str:
    normalized = "".join(text.split())
    for removable in ("トモコ", "智子", "その", "えっと", "あの"):
        normalized = normalized.replace(removable, "")
    return normalized


def _prefix_ratio(left: str, right: str) -> float:
    limit = min(len(left), len(right))
    common = 0
    for index in range(limit):
        if left[index] != right[index]:
            break
        common += 1
    return common / max(len(left), len(right))


def _order_mode_for_action(action: SpeechSchedulerAction) -> SpeechOrderMode:
    if action == SpeechSchedulerAction.APPEND_AFTER_CURRENT:
        return SpeechOrderMode.APPEND_AFTER_CURRENT
    return SpeechOrderMode.REPLACE_CURRENT


def _action_for_emission_decision(decision: SpeechEmissionDecision) -> SpeechSchedulerAction:
    if decision == SpeechEmissionDecision.STOP:
        return SpeechSchedulerAction.STOP
    if decision == SpeechEmissionDecision.APPEND_AFTER_CURRENT:
        return SpeechSchedulerAction.APPEND_AFTER_CURRENT
    if decision in (SpeechEmissionDecision.EMIT_NOW, SpeechEmissionDecision.REPLACE_CURRENT):
        return SpeechSchedulerAction.REPLACE_CURRENT
    return SpeechSchedulerAction.SUPPRESS


def _priority_for_output(output: SpeechSchedulerOutput) -> int:
    return max(0, min(100, int(output.score * 50 + 50)))


def _console_event(event: str, **fields: object) -> None:
    parts = [f"[tomoko:conversation] {event}"]
    for key, value in fields.items():
        text = str(value)
        if len(text) > 120:
            text = text[:117] + "..."
        parts.append(f"{key}={text!r}")
    print(" ".join(parts), flush=True)
