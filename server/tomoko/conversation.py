from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from server.llm.chat import ChatBackend, create_default_real_chat_backend
from server.shared.models import (
    ContextSnapshot,
    ConversationHistoryItem,
    DurableUtterance,
    ModelOutputEvent,
    PartialTranscriptObservation,
    PromptRequest,
    SemanticSaturationResult,
    SpeechOrder,
    SpeechOrderMode,
    SpeechSchedulerAction,
    SpeechSchedulerInput,
    SpeechSchedulerOutput,
)
from server.tomoko.context import ContextSnapshotBuilderV2
from server.tomoko.main import TomokoProcessCore
from server.tomoko.prompt import PromptBuilderV2
from server.tomoko.scheduler import SpeechScheduler, detect_stop_intent
from server.tomoko.semantic import SemanticSaturationJudge
from server.tomoko.session import SessionBoundaryModel


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
    prompt_builder: PromptBuilderV2 = field(default_factory=PromptBuilderV2)
    context_builder: ContextSnapshotBuilderV2 = field(default_factory=ContextSnapshotBuilderV2)
    tomoko_core: TomokoProcessCore | None = None
    current_speech_order: SpeechOrder | None = None
    current_speech_score: float = 0.0
    _recent_utterances: list[str] = field(default_factory=list)
    _recent_history: list[ConversationHistoryItem] = field(default_factory=list)
    _partial_history: list[str] = field(default_factory=list)
    _active_partial_order: SpeechOrder | None = None
    _active_partial_basis_text: str = ""
    _last_reconciled_final_text: str = ""

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
            scheduler_output.reason = (
                "final reconciled with active partial reply"
                if observation.is_final
                else "partial reconciled with active partial reply"
            )
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
        scheduler_output = self.scheduler.decide(
            SpeechSchedulerInput(
                partial_stt_text="" if observation.is_final else basis_text,
                final_stt_text=basis_text if observation.is_final else "",
                stable_prefix=basis_text if observation.is_final else _stable_partial(
                    self._partial_history
                ),
                semantic_saturation=saturation.saturation,
                p_yielding=observation.p_yielding,
                current_speech_order=self.current_speech_order,
                current_speech_score=self.current_speech_score,
                stop_intent=detect_stop_intent(basis_text),
                trace_id=observation.trace_id,
            )
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

        if scheduler_output.action == SpeechSchedulerAction.STOP:
            if durable is not None and prior_session_history is None:
                self._recent_utterances.append(durable.text)
                self._recent_history.append(
                    ConversationHistoryItem(speaker="user", text=durable.text)
                )
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
        if durable is not None and prior_session_history is None:
            self._recent_utterances.append(durable.text)
            self._recent_history.append(
                ConversationHistoryItem(speaker="user", text=durable.text)
            )
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
        if self._active_partial_order is None or not self._active_partial_basis_text:
            return (
                not observation.is_final
                and bool(self._last_reconciled_final_text)
                and _similar_enough(basis_text, self._last_reconciled_final_text)
            )
        if observation.is_final:
            return _similar_enough(self._active_partial_basis_text, basis_text)
        return _similar_enough(self._active_partial_basis_text, basis_text)

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
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=create_default_real_chat_backend(),
    )


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
