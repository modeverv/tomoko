from __future__ import annotations

import logging
import math
import struct
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from server.audio.stt import AppleSpeechStreamingBackend, StreamingSttEvent, observation_events
from server.audio.vad import VADProcessor
from server.hot_path.model_executor import PromptExecutionResult, PromptExecutor
from server.hot_path.speech_executor import SpeechOrderExecutor
from server.shared.models import (
    AudioSpeechSegment,
    ContextSnapshot,
    ConversationHistoryItem,
    DurableUtterance,
    PartialTranscriptObservation,
    PromptRequest,
    SpeechOrder,
    SpeechSchedulerOutput,
    new_id,
)
from server.tomoko.append_dedupe import create_default_append_dedupe_guard
from server.tomoko.context import ContextSnapshotBuilderV2
from server.tomoko.conversation import TomokoConversationCore
from server.tomoko.main import TomokoProcessCore
from server.tomoko.prompt import PromptBuilderV2
from server.tomoko.scheduler import SpeechScheduler
from server.tomoko.semantic import (
    SemanticSaturationJudge,
    create_default_saturation_judge,
)
from server.tomoko.session import SessionBoundaryModel

logger = logging.getLogger(__name__)


class StreamingSttBackend:
    async def transcribe_stream(
        self,
        segment: AudioSpeechSegment,
    ) -> AsyncIterator[StreamingSttEvent]: ...


@dataclass(slots=True)
class HotPathConversationResult:
    observations: list[PartialTranscriptObservation]
    durable_utterance: DurableUtterance | None
    context_snapshot: ContextSnapshot | None
    prompt_request: PromptRequest | None
    execution_result: PromptExecutionResult
    scheduler_output: SpeechSchedulerOutput | None = None
    speech_order: SpeechOrder | None = None


@dataclass(frozen=True, slots=True)
class SegmentSttGateDecision:
    should_transcribe: bool
    reason: str
    duration_ms: float
    rms: float


@dataclass(frozen=True, slots=True)
class SegmentSttGate:
    min_duration_ms: int = 300
    min_rms: float = 0.006

    def inspect(self, segment: AudioSpeechSegment) -> SegmentSttGateDecision:
        duration_ms = len(segment.samples) / segment.sample_rate * 1000.0
        rms = segment_rms(segment.samples)
        too_short = duration_ms < self.min_duration_ms
        too_quiet = rms < self.min_rms
        if too_short and too_quiet:
            reason = "short_and_low_energy"
        elif too_quiet:
            reason = "low_energy"
        else:
            return SegmentSttGateDecision(
                should_transcribe=True,
                reason="accepted",
                duration_ms=duration_ms,
                rms=rms,
            )
        return SegmentSttGateDecision(
            should_transcribe=False,
            reason=reason,
            duration_ms=duration_ms,
            rms=rms,
        )


@dataclass(slots=True)
class HotPathAudioConversation:
    vad: VADProcessor
    stt_backend: StreamingSttBackend
    tomoko_core: TomokoProcessCore | None = None
    prompt_builder: PromptBuilderV2 | None = None
    prompt_executor: PromptExecutor | None = None
    conversation_core: TomokoConversationCore | None = None
    speech_executor: SpeechOrderExecutor | None = None
    speech_rms_threshold: float = 0.02
    stt_gate: SegmentSttGate = field(default_factory=SegmentSttGate)
    context_builder: ContextSnapshotBuilderV2 = field(default_factory=ContextSnapshotBuilderV2)
    _audio_clock_ms: float = field(default_factory=lambda: time.time() * 1000.0)
    _recent_utterances: list[str] = field(default_factory=list)
    _recent_history: list[ConversationHistoryItem] = field(default_factory=list)
    _active_segment_trace_id: UUID | None = None

    async def process_audio_bytes(self, payload: bytes) -> HotPathConversationResult | None:
        return await self.process_audio_samples(audio_bytes_to_samples(payload))

    async def process_audio_samples(
        self,
        samples: tuple[float, ...],
        *,
        process_partials: bool = True,
        partial_callback: Callable[[tuple[float, ...], float], None] | None = None,
        final_callback: Callable[[AudioSpeechSegment], None] | None = None,
    ) -> HotPathConversationResult | None:
        if not samples:
            return None
        now_ms = self._audio_clock_ms
        self._audio_clock_ms += len(samples) / self.vad.sample_rate * 1000.0
        speech_probability = speech_probability_from_rms(
            samples,
            threshold=self.speech_rms_threshold,
        )
        if (
            speech_probability >= self.vad.speech_threshold
            and self._active_segment_trace_id is None
        ):
            self._active_segment_trace_id = new_id()
        segment = self.vad.process_chunk(
            samples,
            speech_probability=speech_probability,
            now_ms=now_ms,
        )
        partial_result = None
        if speech_probability >= self.vad.speech_threshold:
            if partial_callback is not None:
                partial_callback(samples, now_ms)
            elif process_partials:
                partial_result = await self.process_streaming_partial(samples, now_ms)
        if segment is None:
            return partial_result
        if self._active_segment_trace_id is not None:
            segment.trace_id = self._active_segment_trace_id
            self._active_segment_trace_id = None
        self._reset_stt_stream()
        _console_event(
            "vad_segment",
            samples=len(segment.samples),
            sample_rate=segment.sample_rate,
            started_at=segment.started_at.isoformat(),
            ended_at=segment.ended_at.isoformat(),
        )
        gate_decision = self.stt_gate.inspect(segment)
        if not gate_decision.should_transcribe:
            _console_event(
                "vad_segment_dropped",
                reason=gate_decision.reason,
                duration_ms=round(gate_decision.duration_ms, 1),
                rms=round(gate_decision.rms, 6),
                trace_id=str(segment.trace_id),
            )
            return partial_result
        if final_callback is not None:
            final_callback(segment)
            return partial_result
        return await self.process_segment(segment)

    async def process_streaming_partial(
        self,
        samples: tuple[float, ...],
        now_ms: float,
    ) -> HotPathConversationResult | None:
        process_stream_chunk = getattr(self.stt_backend, "process_stream_chunk", None)
        if process_stream_chunk is None:
            return None
        event = await process_stream_chunk(
            samples,
            sample_rate=self.vad.sample_rate,
            started_at_ms=now_ms,
        )
        if event is None:
            return None
        observation = PartialTranscriptObservation(
            text=event.text,
            is_final=False,
            stability=event.stability,
            p_yielding=event.p_yielding,
            recommended_silence_ms=event.recommended_silence_ms,
            audio_started_at=_datetime_from_ms(now_ms),
            audio_ended_at=_datetime_from_ms(
                now_ms + len(samples) / self.vad.sample_rate * 1000.0
            ),
            trace_id=self._active_segment_trace_id or new_id(),
        )
        _console_event("stt_partial", text=observation.text)
        if self.conversation_core is None or self.speech_executor is None:
            return HotPathConversationResult(
                observations=[observation],
                durable_utterance=None,
                context_snapshot=None,
                prompt_request=None,
                execution_result=PromptExecutionResult(),
            )
        turn = await self.conversation_core.handle_observation(observation)
        execution_result = PromptExecutionResult(model_events=list(turn.model_events))
        if turn.speech_order is not None:
            _console_event(
                "partial_speech_order",
                order_id=str(turn.speech_order.id),
                mode=turn.speech_order.mode.value,
                reason=turn.speech_order.reason,
            )
            audio_result = await self.speech_executor.execute(turn.speech_order)
            execution_result.audio_chunks.extend(audio_result.audio_chunks)
        return HotPathConversationResult(
            observations=[observation],
            durable_utterance=None,
            context_snapshot=turn.context_snapshot,
            prompt_request=turn.prompt_request,
            execution_result=execution_result,
            scheduler_output=turn.scheduler_output,
            speech_order=turn.speech_order,
        )

    def _reset_stt_stream(self) -> None:
        reset_stream = getattr(self.stt_backend, "reset_stream", None)
        if reset_stream is not None:
            reset_stream()

    async def process_segment(self, segment: AudioSpeechSegment) -> HotPathConversationResult:
        _console_event("stt_start", samples=len(segment.samples))
        observations = await observation_events(segment, self.stt_backend)
        final_text = next(
            (observation.text for observation in observations if observation.is_final),
            "",
        )
        _console_event("stt_done", observations=len(observations), final_text=final_text)
        final_observation = next(
            (observation for observation in observations if observation.is_final),
            None,
        )
        if self.conversation_core is not None and self.speech_executor is not None:
            observation = final_observation or (observations[-1] if observations else None)
            if observation is None:
                _console_event("stt_no_observation")
                return HotPathConversationResult(
                    observations=observations,
                    durable_utterance=None,
                    context_snapshot=None,
                    prompt_request=None,
                    execution_result=PromptExecutionResult(),
                )
            turn = await self.conversation_core.handle_observation(observation)
            execution_result = PromptExecutionResult(model_events=list(turn.model_events))
            if turn.speech_order is not None:
                _console_event(
                    "speech_order",
                    order_id=str(turn.speech_order.id),
                    mode=turn.speech_order.mode.value,
                    reason=turn.speech_order.reason,
                )
                audio_result = await self.speech_executor.execute(turn.speech_order)
                execution_result.audio_chunks.extend(audio_result.audio_chunks)
            return HotPathConversationResult(
                observations=observations,
                durable_utterance=turn.durable_utterance,
                context_snapshot=turn.context_snapshot,
                prompt_request=turn.prompt_request,
                execution_result=execution_result,
                scheduler_output=turn.scheduler_output,
                speech_order=turn.speech_order,
            )

        assert self.tomoko_core is not None
        assert self.prompt_builder is not None
        assert self.prompt_executor is not None
        durable = (
            self.tomoko_core.adopt_final_observation(final_observation)
            if final_observation is not None
            else None
        )
        if durable is None:
            if final_observation is not None and final_observation.is_final:
                block_reason = self.tomoko_core.block_reason_for_final_observation(
                    final_observation
                )
                logger.info(
                    "final_stt_blocked observation_id=%s reason=%s text=%r",
                    final_observation.id,
                    block_reason,
                    final_observation.text,
                )
                _console_event(
                    "stt_rule_blocked",
                    observation_id=str(final_observation.id),
                    reason=block_reason,
                    text=final_observation.text,
                )
                if block_reason == "blank":
                    _console_event(
                        "blank_final_stt_ignored",
                        observation_id=str(final_observation.id),
                    )
                elif block_reason == "dictionary":
                    _console_event(
                        "stt_hallucination_blocked",
                        observation_id=str(final_observation.id),
                        text=final_observation.text,
                    )
            else:
                _console_event(
                    "stt_no_final",
                    observations=len(observations),
                )
            return HotPathConversationResult(
                observations=observations,
                durable_utterance=None,
                context_snapshot=None,
                prompt_request=None,
                execution_result=PromptExecutionResult(),
            )

        snapshot = self.context_builder.build(
            session_id=durable.session_id,
            recent_utterances=self._recent_utterances[-8:],
            summaries=[],
            calendar_loader=lambda: {},
            user_status=None,
            candidates=[],
            recent_history=self._recent_history[-8:],
        )
        request = self.prompt_builder.build_main_reply(snapshot, durable.text)
        _console_event(
            "prompt_built",
            request_id=str(request.id),
            utterance=durable.text,
        )
        execution_result = await self.prompt_executor.execute(request)
        tomoko_text = text_from_execution_result(execution_result)
        self._recent_utterances.append(durable.text)
        self._recent_history.append(ConversationHistoryItem(speaker="user", text=durable.text))
        if tomoko_text.strip():
            self._recent_history.append(
                ConversationHistoryItem(speaker="tomoko", text=tomoko_text)
            )
        return HotPathConversationResult(
            observations=observations,
            durable_utterance=durable,
            context_snapshot=snapshot,
            prompt_request=request,
            execution_result=execution_result,
        )


def create_default_audio_conversation(prompt_executor: PromptExecutor) -> HotPathAudioConversation:
    chat_backend = prompt_executor._chat_backend
    tts_backend = prompt_executor._tts_backend
    return HotPathAudioConversation(
        vad=VADProcessor(),
        stt_backend=AppleSpeechStreamingBackend(),
        conversation_core=TomokoConversationCore(
            session_model=SessionBoundaryModel(),
            saturation_judge=_default_saturation_judge(),
            scheduler=SpeechScheduler(),
            chat_backend=chat_backend,
            append_dedupe_guard=create_default_append_dedupe_guard(),
            tomoko_core=TomokoProcessCore(SessionBoundaryModel()),
        ),
        speech_executor=SpeechOrderExecutor(tts_backend, protect_inflight_replace=True),
    )


def audio_bytes_to_samples(payload: bytes) -> tuple[float, ...]:
    sample_count = len(payload) // 4
    if sample_count <= 0:
        return ()
    return struct.unpack(f"<{sample_count}f", payload[: sample_count * 4])


def speech_probability_from_rms(samples: tuple[float, ...], *, threshold: float) -> float:
    if not samples or threshold <= 0:
        return 0.0
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    return min(1.0, rms / threshold)


def segment_rms(samples: tuple[float, ...]) -> float:
    if not samples:
        return 0.0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


def text_from_execution_result(result: PromptExecutionResult) -> str:
    return next(
        (event.text for event in result.model_events if event.event_kind == "complete"),
        "",
    )


def _default_saturation_judge() -> SemanticSaturationJudge:
    return create_default_saturation_judge()


def _datetime_from_ms(ms: float) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


def _console_event(event: str, **fields: object) -> None:
    parts = [f"[tomoko:audio] {event}"]
    for key, value in fields.items():
        text = str(value)
        if len(text) > 120:
            text = text[:117] + "..."
        parts.append(f"{key}={text!r}")
    print(" ".join(parts), flush=True)
