from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from server.llm.chat import ChatBackend, create_default_real_chat_backend
from server.shared.models import (
    CancelPolicy,
    CandidateLifecycle,
    CandidateRecord,
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
    PromptScope,
    ResponseKind,
    SemanticSaturationResult,
    SessionSummary,
    SpeechEmissionDecision,
    SpeechEmissionGateInput,
    SpeechOrder,
    SpeechOrderMode,
    SpeechSchedulerAction,
    SpeechSchedulerInput,
    SpeechSchedulerOutput,
    SpeechTextIntent,
    TurnMaterials,
    UserStatusObservation,
    WorldMaterials,
    WorldPressure,
    utc_now,
)
from server.tomoko.append_dedupe import (
    AppendDedupeGuard,
    create_default_append_dedupe_guard,
    decision_score_breakdown,
)
from server.tomoko.calendar import (
    CALENDAR_APPEND_THRESHOLD,
    calendar_notice_text,
    calendar_urgency_from_items,
    next_calendar_item,
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
from server.tomoko.sense import (
    SENSE_KIND_SCREENSHOT,
    SENSE_KIND_WORLD_SEARCH,
    SenseRequestRecord,
)
from server.tomoko.session import SessionBoundaryModel
from server.user_status.main import world_materials_from_user_status

PARTIAL_CONFIRM_SATURATION_THRESHOLD = 0.75
# scheduler 側の partial_start_score_threshold(junk 弁別用)より緩い。
# 通過後も confirm 2回一致 or 依頼完了形サフィックスが必要なので、
# ストリーミング STT の密な partial での先行生成をここで許す。
PARTIAL_START_SCORE_THRESHOLD = 0.65
PARTIAL_CONFIRM_REQUIRED_COUNT = 2
REQUEST_COMPLETE_PARTIAL_SUFFIXES = (
    "教えて",
    "教えてください",
    "して",
    "してください",
    "お願い",
    "お願いします",
    "ほしい",
    "くれる",
    "くれる？",
    "くれる?",
    "？",
    "?",
)
SPEECH_SENTENCE_ENDINGS = frozenset("。！？!?")
REPLY_CONTINUATION_REASON = "reply continuation queued after first sentence"
SENSE_ACK_TEXTS = {
    SENSE_KIND_SCREENSHOT: "ちょっと画面見てみるね。",
    SENSE_KIND_WORLD_SEARCH: "ちょっと調べてみるね。",
}
SENSE_KICK_REASONS = {
    SENSE_KIND_SCREENSHOT: "screenshot sense kicked for user context",
    SENSE_KIND_WORLD_SEARCH: "world search sense kicked for user request",
}
SENSE_FOLLOWUP_REASONS = {
    SENSE_KIND_SCREENSHOT: "sense result follow-up after screenshot",
    SENSE_KIND_WORLD_SEARCH: "sense result follow-up after world search",
}
SENSE_WORLD_SEARCH_FAILED_TEXT = "ごめん、うまく調べられなかったよ。"
SENSE_WORLD_SEARCH_FAILED_REASON = "world search sense returned no result"
SCREENSHOT_SENSE_CUES = ("画面", "スクリーン", "モニタ")
SCREENSHOT_SENSE_PATTERNS = ("何して", "なにして", "何をして", "何やって", "なにやって")
WORLD_SEARCH_SENSE_CUES = ("調べて", "検索して", "ぐぐって", "ググって")
PARTIAL_ACK_REASON = "partial acknowledgement before complete request"
PARTIAL_ACK_TEXT = "うん、聞いてるよ。"
PARTIAL_ACK_SCORE_THRESHOLD = 0.45
PARTIAL_ACK_TOPIC_SCORE_THRESHOLD = 0.30
PARTIAL_ACK_TOPIC_EAGER_SCORE_THRESHOLD = 0.60
MOTIVATION_INTERJECTION_REASON = "motivation interjection before complete request"
MOTIVATION_INTERJECTION_TEXT = "いや、それってさ。"
MOTIVATION_INTERJECTION_SHIFT_THRESHOLD = 0.12
MOTIVATION_INTERJECTION_MAX_SATURATION = 0.65
PARTIAL_ACK_TOPIC_CUES = (
    "予定",
    "天気",
    "何時",
    "時間",
    "会議",
    "優先順位",
    "状態",
    "タスク",
    "リマインド",
    "締め切り",
    "空き時間",
    "メール",
    "昼ごはん",
    "昼ご飯",
    "ごはん",
    "ご飯",
    "おすすめ",
    "お勧め",
    "話",
    "説明",
    "もう一度",
    "もういちど",
    "やるべき",
    "挙げて",
    "あげて",
)
DIRECT_CLOCK_REASON = "direct clock reply from local system time"
DIRECT_CLOCK_CUES = (
    "今何時",
    "いま何時",
    "今なんじ",
    "いまなんじ",
    "今いつ",
    "今の時間",
    "現在時刻",
)
CANDIDATE_INITIATIVE_REASON = "candidate pressure initiative tick"
INITIATIVE_TICK_BASIS_TEXT = "initiative_tick"
INITIATIVE_MIN_SILENCE_MS = 1200
INITIATIVE_MAX_SPEECH_PROBABILITY = 0.2
ATTENTION_MODE_CONVERSATION = "conversation"
ATTENTION_MODE_AMBIENT = "ambient"
ATTENTION_IDLE_SILENCE_MS = 8000
ATTENTION_AMBIENT_MIN_SATURATION = 0.72
ATTENTION_WAKE_CUES = ("トモコ", "ともこ", "tomoko", "智子")
ATTENTION_REQUEST_CUES = (
    "教えて",
    "どう",
    "何",
    "なに",
    "いつ",
    "どこ",
    "誰",
    "だれ",
    "どれ",
    "どっち",
    "予定",
    "会議",
    "天気",
    "時間",
    "おすすめ",
    "お勧め",
    "昼ごはん",
    "昼ご飯",
    "もう一度",
    "もういちど",
    "説明",
    "やるべき",
    "挙げて",
    "あげて",
    "三つ",
    "3つ",
    "?",
    "？",
)


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
    followup_orders: list[SpeechOrder] = field(default_factory=list)


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
    user_status: UserStatusObservation | None = None
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
    _active_partial_ack_basis_text: str = ""
    _last_final_user_text: str = ""
    _last_final_user_audio_ended_at: datetime | None = None
    calendar_items_provider: Callable[[], dict[str, str]] | None = None
    candidate_provider: Callable[[], list[CandidateRecord]] | None = None
    candidate_records: list[CandidateRecord] = field(default_factory=list)
    summary_records: list[SessionSummary] = field(default_factory=list)
    attention_mode: str = ATTENTION_MODE_CONVERSATION
    _notified_calendar_keys: set[str] = field(default_factory=set)
    _continuation_task: asyncio.Task[None] | None = None
    _pending_followup_orders: list[SpeechOrder] = field(default_factory=list)
    sense_executor: Callable[[SenseRequestRecord], object] | None = None
    sense_kinds: frozenset[str] | None = None
    _sense_task: asyncio.Task[None] | None = None
    # decision_generation is owned by this TomokoConversationCore instance
    # (never a module-level counter). It is bumped once per top-level decision
    # cycle (handle_observation / handle_initiative_tick) and stamped onto
    # every SpeechOrder created during that cycle as decision_generation_id.
    # hot-path's playback_generation_id (SpeechOrderExecutionResult) is a
    # separate, hot-path-owned counter; this instance never assigns it.
    _decision_generation: int = 0

    def _bump_decision_generation(self) -> int:
        self._decision_generation += 1
        return self._decision_generation

    def update_turn_materials(self, materials: TurnMaterials) -> None:
        self.turn_materials = materials

    def update_user_status(self, observation: UserStatusObservation) -> None:
        self.user_status = observation
        self.world_materials = world_materials_from_user_status(
            observation,
            base=self.world_materials,
        )

    def update_playback_state(self, playback_active: bool) -> None:
        if playback_active or self.current_speech_order is None:
            return
        self.current_speech_order = None
        self.current_speech_score = 0.0

    def update_candidate_records(self, records: list[CandidateRecord]) -> None:
        self.candidate_records = list(records)

    def update_summary_records(self, records: list[SessionSummary]) -> None:
        self.summary_records = list(records)

    def update_calendar_items_provider(
        self,
        provider: Callable[[], dict[str, str]] | None,
    ) -> None:
        self.calendar_items_provider = provider

    def _update_attention_before_decision(
        self,
        text: str,
        turn_materials: TurnMaterials,
    ) -> None:
        if _is_attention_wake_text(text) or _is_attention_request_text(text):
            self.attention_mode = ATTENTION_MODE_CONVERSATION
            return
        if (
            self.attention_mode == ATTENTION_MODE_CONVERSATION
            and turn_materials.silence_ms >= ATTENTION_IDLE_SILENCE_MS
        ):
            self.attention_mode = ATTENTION_MODE_AMBIENT

    def _attention_should_suppress(self, text: str, *, saturation: float) -> bool:
        return (
            self.attention_mode == ATTENTION_MODE_AMBIENT
            and not _is_attention_wake_text(text)
            and saturation < ATTENTION_AMBIENT_MIN_SATURATION
        )

    def _attention_score_breakdown(self) -> dict[str, float]:
        return {
            "attention_mode_conversation": (
                1.0 if self.attention_mode == ATTENTION_MODE_CONVERSATION else 0.0
            ),
            "attention_mode_ambient": (
                1.0 if self.attention_mode == ATTENTION_MODE_AMBIENT else 0.0
            ),
            "attention_ambient_min_saturation": ATTENTION_AMBIENT_MIN_SATURATION,
        }

    async def handle_initiative_tick(self) -> TomokoConversationResult:
        self._bump_decision_generation()
        now = utc_now()
        observation = PartialTranscriptObservation(
            text="",
            is_final=False,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
        turn_materials = _turn_materials_for_initiative_tick(
            self.turn_materials,
            trace_id=observation.trace_id,
        )
        candidate_records = self._candidate_items()
        self._refresh_candidate_pressure(candidate_records)
        best_candidate = _best_candidate(candidate_records)
        prompt_history = self._recent_history[-8:]
        dialogue_pressure = self.dialogue_pressure_model.calculate(
            turn_materials=turn_materials,
            semantic_saturation=0.0,
        )
        natural_pressure = self.natural_speech_pressure_model.calculate(
            turn_materials=turn_materials,
            personality_materials=self.personality_materials,
        )
        motivation_pressure = self.motivation_pressure_model.calculate(
            turn_materials=turn_materials,
            personality_materials=self.personality_materials,
            recent_history=prompt_history,
            current_text=best_candidate.text if best_candidate is not None else "",
        )
        self._refresh_calendar_urgency()
        world_pressure = self.world_pressure_model.calculate(
            turn_materials=turn_materials,
            world_materials=self.world_materials,
            personality_materials=self.personality_materials,
        )
        saturation = SemanticSaturationResult(
            saturation=0.0,
            source="initiative_tick",
            basis_text=best_candidate.text if best_candidate is not None else "",
            trace_id=observation.trace_id,
        )
        snapshot = self.context_builder.build(
            session_id=None,
            recent_utterances=self._recent_utterances[-8:],
            summaries=list(self.summary_records),
            calendar_loader=self._calendar_items,
            user_status=self.user_status,
            candidates=candidate_records,
            recent_history=prompt_history,
        )
        base_breakdown = _pressure_breakdown(
            dialogue_pressure,
            natural_pressure,
            motivation_pressure,
            world_pressure,
        )
        suppress_reason = _initiative_suppress_reason(
            best_candidate=best_candidate,
            turn_materials=turn_materials,
            world_materials=self.world_materials,
            current_speech_order=self.current_speech_order,
        )
        if suppress_reason:
            return self._initiative_suppressed_result(
                observation=observation,
                saturation=saturation,
                snapshot=snapshot,
                basis_text=INITIATIVE_TICK_BASIS_TEXT,
                reason=suppress_reason,
                score_breakdown=base_breakdown,
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
            text_intent=SpeechTextIntent.INITIATIVE,
            basis_text=INITIATIVE_TICK_BASIS_TEXT,
            reason=llm_fire.reason,
            score=llm_fire.score,
            score_breakdown={
                **llm_fire.score_breakdown,
                **base_breakdown,
            },
            trace_id=observation.trace_id,
        )
        if scheduler_output.action == SpeechSchedulerAction.SUPPRESS:
            return TomokoConversationResult(
                observation=observation,
                durable_utterance=None,
                saturation=saturation,
                scheduler_output=scheduler_output,
                context_snapshot=snapshot,
                prompt_request=None,
                speech_order=None,
            )

        assert best_candidate is not None
        request = self.prompt_builder.build_initiative(snapshot, best_candidate)
        model_events = await self._generate_model_events(request)
        text_out = next(
            (event.text for event in model_events if event.event_kind == "complete"),
            "",
        ).strip()
        prepared = PreparedSpeechCandidate(
            text=text_out,
            priority=max(0.0, min(1.0, scheduler_output.score)),
            freshness=1.0,
            semantic_confidence=world_pressure.candidate_pressure,
            reason=CANDIDATE_INITIATIVE_REASON,
            trace_id=observation.trace_id,
        )
        emission = self.speech_emission_gate.decide(
            SpeechEmissionGateInput(
                candidate=prepared,
                turn_materials=turn_materials,
                dialogue_pressure=dialogue_pressure,
                natural_speech_pressure=natural_pressure,
                motivation_pressure=motivation_pressure,
                world_pressure=world_pressure,
                current_speech_order=self.current_speech_order,
                current_speech_score=self.current_speech_score,
                tomoko_currently_speaking=self.current_speech_order is not None,
                trace_id=observation.trace_id,
            )
        )
        scheduler_output.action = _action_for_emission_decision(emission.decision)
        scheduler_output.reason = (
            CANDIDATE_INITIATIVE_REASON
            if scheduler_output.action != SpeechSchedulerAction.SUPPRESS
            else emission.reason
        )
        scheduler_output.score = emission.score
        scheduler_output.score_breakdown = {
            **scheduler_output.score_breakdown,
            **{f"emission_{key}": value for key, value in emission.score_breakdown.items()},
        }
        if scheduler_output.action == SpeechSchedulerAction.SUPPRESS:
            return TomokoConversationResult(
                observation=observation,
                durable_utterance=None,
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
            response_kind=ResponseKind.CONTENT,
            scheduler_decision_id=scheduler_output.id,
            decision_generation_id=self._decision_generation,
            trace_id=observation.trace_id,
        )
        self.current_speech_order = order
        self.current_speech_score = scheduler_output.score
        if text_out:
            self._recent_history.append(ConversationHistoryItem(speaker="tomoko", text=text_out))
        _console_event(
            "initiative_order_created",
            order_id=str(order.id),
            candidate_id=str(best_candidate.id),
            candidate_source=best_candidate.source,
            candidate_key=best_candidate.source_key,
            chars=len(order.text),
        )
        return TomokoConversationResult(
            observation=observation,
            durable_utterance=None,
            saturation=saturation,
            scheduler_output=scheduler_output,
            context_snapshot=snapshot,
            prompt_request=request,
            speech_order=order,
            model_events=model_events,
        )

    async def handle_observation(
        self,
        observation: PartialTranscriptObservation,
        *,
        session_id_override: UUID | None = None,
        prior_session_history: list[ConversationHistoryItem] | None = None,
    ) -> TomokoConversationResult:
        self._bump_decision_generation()
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

        divergent_final = (
            observation.is_final
            and self._active_partial_order is not None
            and bool(self._active_partial_basis_text)
            and not _similar_enough(self._active_partial_basis_text, basis_text)
        )

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
            if observation.is_final:
                # final が先行 partial 返信に吸収されるケースでもカレンダー通知は
                # 落とさない(poll_orders レーンで再生キューへ append される)
                calendar_followup = self._maybe_calendar_followup(observation.trace_id)
                if calendar_followup is not None:
                    self._pending_followup_orders.append(calendar_followup)
            snapshot = self.context_builder.build(
                session_id=session_id,
                recent_utterances=self._recent_utterances[-8:],
                summaries=list(self.summary_records),
                calendar_loader=self._calendar_items,
                user_status=self.user_status,
                candidates=self._candidate_items(),
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

        if divergent_final and durable is not None:
            self._active_partial_order = None
            self._active_partial_basis_text = ""
            self._last_reconciled_final_text = durable.text
            _console_event(
                "divergent_final_supersedes_partial",
                final_text=durable.text,
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
        prompt_history = (
            prior_session_history
            if prior_session_history is not None
            else self._recent_history[-8:]
        )
        motivation_pressure = self.motivation_pressure_model.calculate(
            turn_materials=turn_materials,
            personality_materials=self.personality_materials,
            recent_history=prompt_history,
            current_text=basis_text,
        )
        self._refresh_calendar_urgency()
        candidate_records = self._candidate_items()
        self._refresh_candidate_pressure(candidate_records)
        world_pressure = self.world_pressure_model.calculate(
            turn_materials=turn_materials,
            world_materials=self.world_materials,
            personality_materials=self.personality_materials,
        )
        self._update_attention_before_decision(basis_text, turn_materials)
        attention_breakdown = self._attention_score_breakdown()
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
                **attention_breakdown,
            },
            trace_id=observation.trace_id,
        )
        snapshot = self.context_builder.build(
            session_id=session_id,
            recent_utterances=self._recent_utterances[-8:],
            summaries=list(self.summary_records),
            calendar_loader=self._calendar_items,
            user_status=self.user_status,
            candidates=candidate_records,
            recent_history=prompt_history,
        )

        if detect_stop_intent(basis_text) >= 0.8:
            self.attention_mode = ATTENTION_MODE_AMBIENT
            scheduler_output = _scheduler_output_from_gate(
                action=SpeechSchedulerAction.STOP,
                text_intent=SpeechTextIntent.STOP,
                basis_text=basis_text,
                reason="stop intent crossed emission threshold",
                score=1.0,
                score_breakdown={"stop_intent": 1.0, **self._attention_score_breakdown()},
                trace_id=observation.trace_id,
            )
        elif self._attention_should_suppress(
            basis_text,
            saturation=saturation.saturation,
        ):
            scheduler_output = _scheduler_output_from_gate(
                action=SpeechSchedulerAction.SUPPRESS,
                text_intent=SpeechTextIntent.REPLY,
                basis_text=basis_text,
                reason="ambient attention suppresses low-saturation speech",
                score=0.0,
                score_breakdown={
                    **_pressure_breakdown(
                        dialogue_pressure,
                        natural_pressure,
                        motivation_pressure,
                        world_pressure,
                    ),
                    **self._attention_score_breakdown(),
                },
                trace_id=observation.trace_id,
            )

        if (
            not observation.is_final
            and scheduler_output.action
            not in (SpeechSchedulerAction.SUPPRESS, SpeechSchedulerAction.STOP)
            and self._motivation_interjection_allows(
                basis_text,
                saturation=saturation.saturation,
                turn_materials=turn_materials,
                motivation_pressure=motivation_pressure,
            )
        ):
            return self._motivation_interjection_result(
                observation=observation,
                saturation=saturation,
                scheduler_output=scheduler_output,
                snapshot=snapshot,
                basis_text=basis_text,
            )

        if (
            not observation.is_final
            and scheduler_output.action == SpeechSchedulerAction.SUPPRESS
            and self._partial_ack_allows(basis_text, score=scheduler_output.score)
        ):
            return self._partial_ack_result(
                observation=observation,
                saturation=saturation,
                scheduler_output=scheduler_output,
                snapshot=snapshot,
                basis_text=basis_text,
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
                turn_materials=turn_materials,
            )
        ):
            if self._partial_ack_after_start_gate_allows(
                basis_text,
                turn_materials=turn_materials,
                score=scheduler_output.score,
                score_breakdown=scheduler_output.score_breakdown,
            ):
                return self._partial_ack_result(
                    observation=observation,
                    saturation=saturation,
                    scheduler_output=scheduler_output,
                    snapshot=snapshot,
                    basis_text=basis_text,
                )
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
            self._cancel_reply_continuation()
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
                response_kind=None,
                scheduler_decision_id=scheduler_output.id,
                decision_generation_id=self._decision_generation,
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

        direct_text = _direct_clock_reply_text(basis_text) if observation.is_final else None
        if direct_text:
            return self._direct_speech_result(
                observation=observation,
                durable=durable,
                saturation=saturation,
                scheduler_output=scheduler_output,
                snapshot=snapshot,
                text_out=direct_text,
                reason=DIRECT_CLOCK_REASON,
                prior_session_history=prior_session_history,
            )

        sense_kind = _sense_kind_for_text(basis_text) if observation.is_final else None
        if sense_kind is not None and self.sense_kinds is not None:
            if sense_kind not in self.sense_kinds:
                sense_kind = None
        if sense_kind is not None and self.sense_executor is not None:
            return self._sense_kick_result(
                observation=observation,
                durable=durable,
                saturation=saturation,
                scheduler_output=scheduler_output,
                snapshot=snapshot,
                basis_text=basis_text,
                sense_kind=sense_kind,
                prior_session_history=prior_session_history,
            )

        request = self.prompt_builder.build_main_reply(
            snapshot,
            basis_text,
            concise=not observation.is_final,
        )
        reply_stream = self.chat_backend.stream(request)
        model_events, continuation_tail = await self._collect_first_sentence_events(
            request,
            reply_stream,
        )
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
                current_speech_score=0.0 if divergent_final else self.current_speech_score,
                tomoko_currently_speaking=self.current_speech_order is not None,
                stop_intent=detect_stop_intent(basis_text),
                trace_id=observation.trace_id,
            )
        )
        scheduler_output.action = _action_for_emission_decision(emission.decision)
        scheduler_output.reason = emission.reason
        scheduler_output.score = emission.score
        if divergent_final and scheduler_output.action in (
            SpeechSchedulerAction.REPLACE_CURRENT,
            SpeechSchedulerAction.APPEND_AFTER_CURRENT,
        ):
            scheduler_output.action = SpeechSchedulerAction.REPLACE_CURRENT
            scheduler_output.reason = "final diverged from active partial reply; replacing"
        scheduler_output.score_breakdown = {
            **scheduler_output.score_breakdown,
            **{f"emission_{key}": value for key, value in emission.score_breakdown.items()},
        }
        if scheduler_output.action == SpeechSchedulerAction.SUPPRESS:
            await _close_stream(reply_stream)
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
            response_kind=ResponseKind.CORRECTION if divergent_final else ResponseKind.CONTENT,
            scheduler_decision_id=scheduler_output.id,
            decision_generation_id=self._decision_generation,
            trace_id=observation.trace_id,
        )
        self._cancel_reply_continuation()
        if observation.is_final:
            self._start_reply_continuation(
                stream=reply_stream,
                tail=continuation_tail,
                priority=order.priority,
                decision_id=scheduler_output.id,
                trace_id=observation.trace_id,
            )
        else:
            await _close_stream(reply_stream)
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
        followup_orders: list[SpeechOrder] = []
        if observation.is_final:
            calendar_followup = self._maybe_calendar_followup(observation.trace_id)
            if calendar_followup is not None:
                followup_orders.append(calendar_followup)
        return TomokoConversationResult(
            observation=observation,
            durable_utterance=durable,
            saturation=saturation,
            scheduler_output=scheduler_output,
            context_snapshot=snapshot,
            prompt_request=request,
            speech_order=order,
            model_events=model_events,
            followup_orders=followup_orders,
        )

    def _calendar_items(self) -> dict[str, str]:
        if self.calendar_items_provider is None:
            return {}
        return dict(self.calendar_items_provider())

    def _candidate_items(self) -> list[CandidateRecord]:
        records = list(self.candidate_records)
        if self.candidate_provider is not None:
            records.extend(self.candidate_provider())
        return [
            candidate
            for candidate in records
            if candidate.lifecycle == CandidateLifecycle.ACTIVE
        ]

    def _refresh_candidate_pressure(self, candidates: list[CandidateRecord]) -> None:
        self.world_materials.candidate_pressure = max(
            (candidate.candidate_score for candidate in candidates),
            default=0.0,
        )

    def _refresh_calendar_urgency(self) -> None:
        if self.calendar_items_provider is None:
            return
        computed = calendar_urgency_from_items(self._calendar_items(), now=utc_now())
        if computed > self.world_materials.calendar_urgency:
            self.world_materials.calendar_urgency = computed

    def _maybe_calendar_followup(self, trace_id: UUID) -> SpeechOrder | None:
        if not self.world_materials.user_present:
            return None
        items = self._calendar_items()
        if not items:
            return None
        now = utc_now()
        urgency = max(
            calendar_urgency_from_items(items, now=now),
            self.world_materials.calendar_urgency,
        )
        if urgency < CALENDAR_APPEND_THRESHOLD:
            return None
        nearest = next_calendar_item(items, now=now)
        if nearest is None or nearest[0] in self._notified_calendar_keys:
            return None
        key, title = nearest
        self._notified_calendar_keys.add(key)
        order = SpeechOrder(
            text=calendar_notice_text(key, title),
            mode=SpeechOrderMode.APPEND_AFTER_CURRENT,
            reason=f"calendar pressure appended notice after reply (urgency={urgency:.2f})",
            priority=max(0, min(100, int(50 + urgency * 40))),
            response_kind=ResponseKind.CONTENT,
            decision_generation_id=self._decision_generation,
            trace_id=trace_id,
        )
        _console_event(
            "calendar_followup_order",
            order_id=str(order.id),
            starts_at=key,
            title=title,
            urgency=round(urgency, 3),
        )
        return order

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
        turn_materials: TurnMaterials,
    ) -> bool:
        if (
            saturation < PARTIAL_CONFIRM_SATURATION_THRESHOLD
            and score < PARTIAL_START_SCORE_THRESHOLD
        ):
            self._partial_start_confirm_text = ""
            self._partial_start_confirm_count = 0
            self._partial_start_gate_last_reason = (
                "partial start gate is below confirmation thresholds"
            )
            return False
        if _looks_request_complete_partial(basis_text):
            self._partial_start_confirm_text = basis_text
            self._partial_start_confirm_count = PARTIAL_CONFIRM_REQUIRED_COUNT
            return self._partial_start_gate_yield_check(turn_materials)
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
        if self._partial_start_confirm_count < PARTIAL_CONFIRM_REQUIRED_COUNT:
            self._partial_start_gate_last_reason = (
                "partial start gate is waiting for confirmation"
            )
            return False
        return self._partial_start_gate_yield_check(turn_materials)

    def _partial_start_gate_yield_check(self, turn_materials: TurnMaterials) -> bool:
        if (
            turn_materials.user_speaking
            and turn_materials.speech_probability > 0.3
            and (turn_materials.p_yielding or 0.0) < 0.5
            and turn_materials.silence_ms < 300
        ):
            # ユーザーがまだ話し続けている(譲る気配がない)間は、
            # フル返信レーンの先行生成でユーザーを遮らない。相槌は ack レーンが担う。
            self._partial_start_gate_last_reason = (
                "partial start gate is waiting for user to yield"
            )
            return False
        self._partial_start_gate_last_reason = ""
        return True

    async def _generate_model_events(self, request: PromptRequest) -> list[ModelOutputEvent]:
        events: list[ModelOutputEvent] = []
        parts: list[str] = []
        async for delta in self.chat_backend.stream(request):
            if not delta:
                continue
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

    async def _collect_first_sentence_events(
        self,
        request: PromptRequest,
        stream: AsyncIterator[str],
    ) -> tuple[list[ModelOutputEvent], str]:
        """最初の文までを即時イベント化し、文境界を跨いだ余りを tail として返す。

        stream は消費し切らずに返すので、final 返信では続きを
        _start_reply_continuation でバックグラウンド生成できる。
        """
        events: list[ModelOutputEvent] = []
        parts: list[str] = []
        tail = ""
        async for delta in stream:
            if not delta:
                continue
            current_text = "".join(parts)
            candidate_text = f"{current_text}{delta}"
            cutoff = _first_sentence_cutoff(candidate_text)
            if cutoff is None:
                emit_delta = delta
            else:
                first_sentence = candidate_text[:cutoff].strip()
                emit_delta = first_sentence[len(current_text) :]
                tail = candidate_text[cutoff:]
            if emit_delta:
                parts.append(emit_delta)
                events.append(
                    ModelOutputEvent(
                        request_id=request.id,
                        event_kind="delta",
                        text_delta=emit_delta,
                        trace_id=request.trace_id,
                    )
                )
            if cutoff is not None:
                break
        full_text = "".join(parts)
        events.append(
            ModelOutputEvent(
                request_id=request.id,
                event_kind="complete",
                text=full_text,
                trace_id=request.trace_id,
            )
        )
        return events, tail

    def _start_reply_continuation(
        self,
        *,
        stream: AsyncIterator[str],
        tail: str,
        priority: int,
        decision_id: UUID,
        trace_id: UUID,
    ) -> None:
        self._continuation_task = asyncio.create_task(
            self._consume_reply_continuation(
                stream=stream,
                tail=tail,
                priority=priority,
                decision_id=decision_id,
                trace_id=trace_id,
            )
        )

    async def _consume_reply_continuation(
        self,
        *,
        stream: AsyncIterator[str],
        tail: str,
        priority: int,
        decision_id: UUID,
        trace_id: UUID,
    ) -> None:
        parts: list[str] = [tail]
        try:
            async for delta in stream:
                if delta:
                    parts.append(delta)
        finally:
            await _close_stream(stream)
        text = "".join(parts).strip()
        if not text:
            return
        order = SpeechOrder(
            text=text,
            mode=SpeechOrderMode.APPEND_AFTER_CURRENT,
            reason=REPLY_CONTINUATION_REASON,
            priority=priority,
            response_kind=ResponseKind.CONTENT,
            scheduler_decision_id=decision_id,
            decision_generation_id=self._decision_generation,
            trace_id=trace_id,
        )
        self._pending_followup_orders.append(order)
        self._recent_history.append(ConversationHistoryItem(speaker="tomoko", text=text))
        _console_event(
            "reply_continuation_ready",
            order_id=str(order.id),
            chars=len(text),
        )

    def _queue_world_search_apology(self, record: SenseRequestRecord) -> None:
        order = SpeechOrder(
            text=SENSE_WORLD_SEARCH_FAILED_TEXT,
            mode=SpeechOrderMode.APPEND_AFTER_CURRENT,
            reason=SENSE_WORLD_SEARCH_FAILED_REASON,
            priority=50,
            response_kind=ResponseKind.FOLLOWUP,
            decision_generation_id=self._decision_generation,
            trace_id=record.trace_id,
        )
        self._pending_followup_orders.append(order)
        self._recent_history.append(
            ConversationHistoryItem(speaker="tomoko", text=order.text)
        )
        _console_event(
            "sense_apology_queued",
            request_id=str(record.id),
            order_id=str(order.id),
        )

    def _cancel_reply_continuation(self) -> None:
        for task in (self._continuation_task, self._sense_task):
            if task is not None and not task.done():
                task.cancel()
        self._continuation_task = None
        self._sense_task = None
        self._pending_followup_orders.clear()

    def has_pending_followup_work(self) -> bool:
        if self._pending_followup_orders:
            return True
        return any(
            task is not None and not task.done()
            for task in (self._continuation_task, self._sense_task)
        )

    def poll_followup_orders(self) -> tuple[list[SpeechOrder], bool]:
        orders = list(self._pending_followup_orders)
        self._pending_followup_orders.clear()
        pending = any(
            task is not None and not task.done()
            for task in (self._continuation_task, self._sense_task)
        )
        return orders, pending

    def _sense_kick_result(
        self,
        *,
        observation: PartialTranscriptObservation,
        durable: DurableUtterance | None,
        saturation: SemanticSaturationResult,
        scheduler_output: SpeechSchedulerOutput,
        snapshot: ContextSnapshot,
        basis_text: str,
        sense_kind: str,
        prior_session_history: list[ConversationHistoryItem] | None,
    ) -> TomokoConversationResult:
        self._cancel_reply_continuation()
        scheduler_output.action = SpeechSchedulerAction.REPLACE_CURRENT
        record = SenseRequestRecord(
            kind=sense_kind,
            query=basis_text,
            requested_by="conversation_core",
            trace_id=observation.trace_id,
        )
        result = self._direct_speech_result(
            observation=observation,
            durable=durable,
            saturation=saturation,
            scheduler_output=scheduler_output,
            snapshot=snapshot,
            text_out=SENSE_ACK_TEXTS[sense_kind],
            reason=SENSE_KICK_REASONS[sense_kind],
            prior_session_history=prior_session_history,
            breakdown_key="sense_kick",
            response_kind=ResponseKind.ACKNOWLEDGEMENT,
        )
        _console_event(
            "sense_request_kicked",
            request_id=str(record.id),
            kind=record.kind,
            query=basis_text[:60],
        )
        self._sense_task = asyncio.create_task(
            self._run_sense_followup(record=record, basis_text=basis_text)
        )
        return result

    async def _run_sense_followup(
        self,
        *,
        record: SenseRequestRecord,
        basis_text: str,
    ) -> None:
        executor = self.sense_executor
        if executor is None:
            return
        try:
            result = await executor(record)  # type: ignore[misc]
        except Exception as exc:
            _console_event(
                "sense_executor_failed",
                request_id=str(record.id),
                error=type(exc).__name__,
                message=str(exc),
            )
            return
        if not result:
            _console_event(
                "sense_followup_skipped",
                request_id=str(record.id),
                reason="no_result",
            )
            if record.kind == SENSE_KIND_WORLD_SEARCH:
                self._queue_world_search_apology(record)
            return
        extra_candidates: list[CandidateRecord] = []
        if record.kind == SENSE_KIND_SCREENSHOT:
            status = UserStatusObservation(
                present=bool(result.get("present", True)),
                activity_label=str(result.get("activity_label", "unknown_activity")),
                summary=str(result.get("summary", "")),
                source="sense_screenshot",
                confidence=float(result.get("confidence", 0.5)),
                visible_text=str(result.get("visible_text", ""))[:400],
                app_name=result.get("app_name"),
                window_title=result.get("window_title"),
            )
            self.update_user_status(status)
        elif record.kind == SENSE_KIND_WORLD_SEARCH:
            texts = [str(text).strip() for text in (result.get("texts") or [])]
            texts = [text for text in texts if text]
            if not texts:
                _console_event(
                    "sense_followup_skipped",
                    request_id=str(record.id),
                    reason="empty_search_result",
                )
                self._queue_world_search_apology(record)
                return
            extra_candidates = [
                CandidateRecord(
                    seed_id=uuid4(),
                    source="world_search",
                    source_key=f"sense-{record.id.hex[:8]}-{index}",
                    text=text,
                    priority=0.7,
                    urgency=0.2,
                    intrusion=0.1,
                    maturity=1.0,
                    lifecycle=CandidateLifecycle.ACTIVE,
                    context_tags=("world_search",),
                )
                for index, text in enumerate(texts[:3])
            ]
        snapshot = self.context_builder.build(
            session_id=None,
            recent_utterances=self._recent_utterances[-8:],
            summaries=list(self.summary_records),
            calendar_loader=self._calendar_items,
            user_status=self.user_status,
            candidates=[*self._candidate_items(), *extra_candidates],
            recent_history=self._recent_history[-8:],
        )
        request = self.prompt_builder.build_main_reply(snapshot, basis_text)
        model_events = await self._generate_model_events(request)
        text = next(
            (event.text for event in model_events if event.event_kind == "complete"),
            "",
        ).strip()
        if not text:
            return
        order = SpeechOrder(
            text=text,
            mode=SpeechOrderMode.APPEND_AFTER_CURRENT,
            reason=SENSE_FOLLOWUP_REASONS.get(record.kind, "sense result follow-up"),
            priority=60,
            response_kind=ResponseKind.FOLLOWUP,
            decision_generation_id=self._decision_generation,
            trace_id=record.trace_id,
        )
        self._pending_followup_orders.append(order)
        self._recent_history.append(ConversationHistoryItem(speaker="tomoko", text=text))
        _console_event(
            "sense_followup_ready",
            request_id=str(record.id),
            order_id=str(order.id),
            chars=len(text),
        )

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
        self._active_partial_ack_basis_text = ""

    def _partial_ack_allows(self, basis_text: str, *, score: float) -> bool:
        compact = basis_text.strip()
        has_topic_cue = _has_partial_ack_topic_cue(compact)
        threshold = (
            PARTIAL_ACK_TOPIC_SCORE_THRESHOLD
            if has_topic_cue
            else PARTIAL_ACK_SCORE_THRESHOLD
        )
        if score < threshold:
            return False
        min_len = 2 if has_topic_cue else 4
        if len(compact) < min_len:
            return False
        if _looks_request_complete_partial(compact):
            return False
        if detect_stop_intent(compact) >= 0.8:
            return False
        if self._active_partial_ack_basis_text and _similar_enough(
            self._active_partial_ack_basis_text, compact
        ):
            return False
        return True

    def _partial_ack_after_start_gate_allows(
        self,
        basis_text: str,
        *,
        turn_materials: TurnMaterials,
        score: float,
        score_breakdown: dict[str, float],
    ) -> bool:
        if not self._partial_ack_allows(basis_text, score=score):
            return False
        if (
            _has_partial_ack_topic_cue(basis_text)
            and score >= PARTIAL_ACK_TOPIC_EAGER_SCORE_THRESHOLD
        ):
            return True
        if turn_materials.silence_ms >= 800:
            return True
        if turn_materials.speech_probability <= 0.25:
            return True
        return score_breakdown.get("pressure_natural_filler_desire", 0.0) >= 0.5

    def _partial_ack_result(
        self,
        *,
        observation: PartialTranscriptObservation,
        saturation: SemanticSaturationResult,
        scheduler_output: SpeechSchedulerOutput,
        snapshot: ContextSnapshot,
        basis_text: str,
    ) -> TomokoConversationResult:
        self._cancel_reply_continuation()
        scheduler_output.action = SpeechSchedulerAction.REPLACE_CURRENT
        scheduler_output.reason = PARTIAL_ACK_REASON
        scheduler_output.score_breakdown = {
            **scheduler_output.score_breakdown,
            "partial_ack": 1.0,
        }
        order = SpeechOrder(
            text=PARTIAL_ACK_TEXT,
            mode=SpeechOrderMode.REPLACE_CURRENT,
            reason=PARTIAL_ACK_REASON,
            priority=35,
            response_kind=ResponseKind.ACKNOWLEDGEMENT,
            scheduler_decision_id=scheduler_output.id,
            decision_generation_id=self._decision_generation,
            trace_id=observation.trace_id,
        )
        request = PromptRequest(
            prompt_text=order.text,
            scope=PromptScope.SHORT,
            decision_id=order.scheduler_decision_id,
            utterance_id=None,
            candidate_id=None,
            priority=order.priority,
            cancel_policy=CancelPolicy.KEEP_UNTIL_COMPLETE,
            id=order.id,
            trace_id=order.trace_id,
        )
        self.current_speech_order = order
        self.current_speech_score = 0.1
        self._active_partial_ack_basis_text = basis_text
        _console_event(
            "partial_ack_order_created",
            order_id=str(order.id),
            chars=len(order.text),
            basis_text=basis_text,
        )
        return TomokoConversationResult(
            observation=observation,
            durable_utterance=None,
            saturation=saturation,
            scheduler_output=scheduler_output,
            context_snapshot=snapshot,
            prompt_request=request,
            speech_order=order,
            model_events=[
                ModelOutputEvent(
                    request_id=order.id,
                    event_kind="complete",
                    text=order.text,
                    trace_id=order.trace_id,
                )
            ],
        )

    def _motivation_interjection_allows(
        self,
        basis_text: str,
        *,
        saturation: float,
        turn_materials: TurnMaterials,
        motivation_pressure: MotivationPressure,
    ) -> bool:
        compact = basis_text.strip()
        if len(compact) < 4:
            return False
        if _looks_request_complete_partial(compact):
            return False
        if detect_stop_intent(compact) >= 0.8:
            return False
        if saturation > MOTIVATION_INTERJECTION_MAX_SATURATION:
            return False
        if motivation_pressure.threshold_shift < MOTIVATION_INTERJECTION_SHIFT_THRESHOLD:
            return False
        return (turn_materials.p_yielding or 0.0) >= 0.7

    def _motivation_interjection_result(
        self,
        *,
        observation: PartialTranscriptObservation,
        saturation: SemanticSaturationResult,
        scheduler_output: SpeechSchedulerOutput,
        snapshot: ContextSnapshot,
        basis_text: str,
    ) -> TomokoConversationResult:
        scheduler_output.action = SpeechSchedulerAction.REPLACE_CURRENT
        scheduler_output.reason = MOTIVATION_INTERJECTION_REASON
        scheduler_output.score_breakdown = {
            **scheduler_output.score_breakdown,
            "motivation_interjection": 1.0,
        }
        order = SpeechOrder(
            text=MOTIVATION_INTERJECTION_TEXT,
            mode=SpeechOrderMode.REPLACE_CURRENT,
            reason=MOTIVATION_INTERJECTION_REASON,
            priority=45,
            response_kind=ResponseKind.ACKNOWLEDGEMENT,
            scheduler_decision_id=scheduler_output.id,
            decision_generation_id=self._decision_generation,
            trace_id=observation.trace_id,
        )
        request = PromptRequest(
            prompt_text=order.text,
            scope=PromptScope.SHORT,
            decision_id=order.scheduler_decision_id,
            utterance_id=None,
            candidate_id=None,
            priority=order.priority,
            cancel_policy=CancelPolicy.KEEP_UNTIL_COMPLETE,
            id=order.id,
            trace_id=order.trace_id,
        )
        self.current_speech_order = order
        self.current_speech_score = 0.2
        _console_event(
            "motivation_interjection_order_created",
            order_id=str(order.id),
            chars=len(order.text),
            basis_text=basis_text,
        )
        return TomokoConversationResult(
            observation=observation,
            durable_utterance=None,
            saturation=saturation,
            scheduler_output=scheduler_output,
            context_snapshot=snapshot,
            prompt_request=request,
            speech_order=order,
            model_events=[
                ModelOutputEvent(
                    request_id=order.id,
                    event_kind="complete",
                    text=order.text,
                    trace_id=order.trace_id,
                )
            ],
        )

    def _direct_speech_result(
        self,
        *,
        observation: PartialTranscriptObservation,
        durable: DurableUtterance | None,
        saturation: SemanticSaturationResult,
        scheduler_output: SpeechSchedulerOutput,
        snapshot: ContextSnapshot,
        text_out: str,
        reason: str,
        prior_session_history: list[ConversationHistoryItem] | None,
        breakdown_key: str = "direct_clock",
        response_kind: ResponseKind = ResponseKind.CONTENT,
    ) -> TomokoConversationResult:
        scheduler_output.reason = reason
        scheduler_output.score_breakdown = {
            **scheduler_output.score_breakdown,
            breakdown_key: 1.0,
        }
        order = SpeechOrder(
            text=text_out,
            mode=_order_mode_for_action(scheduler_output.action),
            reason=reason,
            priority=_priority_for_output(scheduler_output),
            response_kind=response_kind,
            scheduler_decision_id=scheduler_output.id,
            decision_generation_id=self._decision_generation,
            trace_id=observation.trace_id,
        )
        request = PromptRequest(
            prompt_text=order.text,
            scope=PromptScope.SHORT,
            decision_id=order.scheduler_decision_id,
            utterance_id=durable.id if durable is not None else None,
            candidate_id=None,
            priority=order.priority,
            cancel_policy=CancelPolicy.KEEP_UNTIL_COMPLETE,
            id=order.id,
            trace_id=order.trace_id,
        )
        self.current_speech_order = order
        self.current_speech_score = scheduler_output.score
        if durable is not None and prior_session_history is None:
            self._recent_utterances.append(durable.text)
            self._recent_history.append(
                ConversationHistoryItem(speaker="user", text=durable.text)
            )
            self._recent_history.append(
                ConversationHistoryItem(speaker="tomoko", text=text_out)
            )
        if durable is not None:
            self._remember_final_user(durable.text, observation)
        _console_event(
            "direct_speech_order_created",
            order_id=str(order.id),
            reason=reason,
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
            model_events=[
                ModelOutputEvent(
                    request_id=order.id,
                    event_kind="complete",
                    text=order.text,
                    trace_id=order.trace_id,
                )
            ],
        )

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

    def _initiative_suppressed_result(
        self,
        *,
        observation: PartialTranscriptObservation,
        saturation: SemanticSaturationResult,
        snapshot: ContextSnapshot,
        basis_text: str,
        reason: str,
        score_breakdown: dict[str, float],
    ) -> TomokoConversationResult:
        scheduler_output = _scheduler_output_from_gate(
            action=SpeechSchedulerAction.SUPPRESS,
            text_intent=SpeechTextIntent.INITIATIVE,
            basis_text=basis_text,
            reason=reason,
            score=0.0,
            score_breakdown=score_breakdown,
            trace_id=observation.trace_id,
        )
        return TomokoConversationResult(
            observation=observation,
            durable_utterance=None,
            saturation=saturation,
            scheduler_output=scheduler_output,
            context_snapshot=snapshot,
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
    observation_silence_ms = observation.recommended_silence_ms or (
        400 if observation.is_final else 0
    )
    if current is None:
        return TurnMaterials(
            window_ms=200,
            user_speaking=not observation.is_final,
            speech_probability=0.5 if not observation.is_final else 0.0,
            p_yielding=observation.p_yielding,
            silence_ms=observation_silence_ms,
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
        else None,
        silence_ms=max(current.silence_ms, observation_silence_ms),
        playback_active=current.playback_active,
        p_bc_react=current.p_bc_react,
        p_bc_emo=current.p_bc_emo,
        audio_rms=current.audio_rms,
        stt_partial=basis_text if not observation.is_final else current.stt_partial,
        trace_id=observation.trace_id,
    )


def _turn_materials_for_initiative_tick(
    current: TurnMaterials | None,
    *,
    trace_id: UUID,
) -> TurnMaterials:
    if current is None:
        return TurnMaterials(
            window_ms=200,
            user_speaking=False,
            speech_probability=0.0,
            p_yielding=None,
            silence_ms=3000,
            playback_active=False,
            trace_id=trace_id,
        )
    return TurnMaterials(
        window_ms=current.window_ms,
        user_speaking=current.user_speaking,
        speech_probability=current.speech_probability,
        p_yielding=current.p_yielding,
        silence_ms=current.silence_ms,
        playback_active=current.playback_active,
        p_bc_react=current.p_bc_react,
        p_bc_emo=current.p_bc_emo,
        audio_rms=current.audio_rms,
        stt_partial=current.stt_partial,
        trace_id=trace_id,
    )


def _best_candidate(candidates: list[CandidateRecord]) -> CandidateRecord | None:
    return max(
        candidates,
        key=lambda candidate: (
            candidate.candidate_score,
            candidate.urgency,
            candidate.priority,
        ),
        default=None,
    )


def _initiative_suppress_reason(
    *,
    best_candidate: CandidateRecord | None,
    turn_materials: TurnMaterials,
    world_materials: WorldMaterials,
    current_speech_order: SpeechOrder | None,
) -> str:
    if best_candidate is None:
        return "no active candidates for initiative tick"
    if not world_materials.user_present:
        return "user absence suppresses initiative tick"
    if current_speech_order is not None or turn_materials.playback_active:
        return "current speech suppresses initiative tick"
    if turn_materials.user_speaking:
        return "user speech suppresses initiative tick"
    if turn_materials.speech_probability > INITIATIVE_MAX_SPEECH_PROBABILITY:
        return "user audio energy suppresses initiative tick"
    if (
        turn_materials.silence_ms < INITIATIVE_MIN_SILENCE_MS
        and (turn_materials.p_yielding or 0.0) < 0.7
    ):
        return "silence is too short for initiative tick"
    return ""


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
        "pressure_dialogue_yielding_opportunity": dialogue.yielding_opportunity,
        "pressure_dialogue_silence_opportunity": dialogue.silence_opportunity,
        "pressure_dialogue_turn_opportunity_from_yielding": (
            1.0
            if dialogue.yielding_opportunity >= dialogue.silence_opportunity
            and dialogue.yielding_opportunity > 0.0
            else 0.0
        ),
        "pressure_dialogue_turn_opportunity_from_silence": (
            1.0
            if dialogue.silence_opportunity > dialogue.yielding_opportunity
            and dialogue.silence_opportunity > 0.0
            else 0.0
        ),
        "pressure_dialogue_interruption_risk": dialogue.interruption_risk,
        "pressure_natural_backchannel_desire": natural.backchannel_desire,
        "pressure_natural_light_reaction_desire": natural.light_reaction_desire,
        "pressure_natural_filler_desire": natural.filler_desire,
        "pressure_motivation_initiative_desire": motivation.initiative_desire,
        "pressure_motivation_personality_push": motivation.personality_push,
        "pressure_motivation_conversation_heat": motivation.conversation_heat,
        "pressure_motivation_topic_continuity": motivation.topic_continuity,
        "pressure_motivation_threshold_shift": motivation.threshold_shift,
        "pressure_world_importance": world.importance,
        "pressure_world_urgency": world.urgency,
        "pressure_world_deliverability": world.deliverability,
        "pressure_world_candidate_pressure": world.candidate_pressure,
        "pressure_world_user_presence": world.user_presence,
        "pressure_world_user_absence": world.user_absence,
    }


def _stable_partial(partials: list[str]) -> str:
    if not partials:
        return ""
    prefix = partials[0]
    for partial in partials[1:]:
        while prefix and not partial.startswith(prefix):
            prefix = prefix[:-1]
    return prefix


def _looks_request_complete_partial(text: str) -> bool:
    compact = text.strip()
    if len(compact) < 6:
        return False
    return any(compact.endswith(suffix) for suffix in REQUEST_COMPLETE_PARTIAL_SUFFIXES)


def _has_partial_ack_topic_cue(text: str) -> bool:
    return any(cue in text for cue in PARTIAL_ACK_TOPIC_CUES)


def _direct_clock_reply_text(text: str) -> str | None:
    compact = text.translate(str.maketrans("", "", " 　、。，．?？!！"))
    if not any(cue in compact for cue in DIRECT_CLOCK_CUES):
        return None
    now = datetime.now().astimezone()
    return f"今は{now.hour}時{now.minute:02d}分だよ。"


def _is_attention_wake_text(text: str) -> bool:
    compact = text.lower().translate(str.maketrans("", "", " 　、。，．?？!！"))
    return any(cue.lower() in compact for cue in ATTENTION_WAKE_CUES)


def _is_attention_request_text(text: str) -> bool:
    compact = text.strip()
    return any(cue in compact for cue in ATTENTION_REQUEST_CUES)


def _first_sentence_cutoff(text: str) -> int | None:
    for index, char in enumerate(text):
        if char in SPEECH_SENTENCE_ENDINGS:
            return index + 1
    return None


def _wants_screenshot_sense(text: str) -> bool:
    compact = "".join(text.split())
    if any(cue in compact for cue in SCREENSHOT_SENSE_CUES):
        return True
    return any(pattern in compact for pattern in SCREENSHOT_SENSE_PATTERNS)


def _wants_world_search_sense(text: str) -> bool:
    compact = "".join(text.split())
    return any(cue in compact for cue in WORLD_SEARCH_SENSE_CUES)


def _sense_kind_for_text(text: str) -> str | None:
    if _wants_screenshot_sense(text):
        return SENSE_KIND_SCREENSHOT
    if _wants_world_search_sense(text):
        return SENSE_KIND_WORLD_SEARCH
    return None


async def _close_stream(stream: AsyncIterator[str]) -> None:
    aclose = getattr(stream, "aclose", None)
    if aclose is None:
        return
    with suppress(Exception):
        await aclose()


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
