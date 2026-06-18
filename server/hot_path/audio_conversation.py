from __future__ import annotations

import logging
import math
import struct
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from server.audio.stt import AppleSpeechStreamingBackend, StreamingSttEvent, observation_events
from server.audio.vad import VADProcessor
from server.hot_path.model_executor import PromptExecutionResult, PromptExecutor
from server.shared.models import (
    AudioSpeechSegment,
    ContextSnapshot,
    DurableUtterance,
    PartialTranscriptObservation,
    PromptRequest,
)
from server.tomoko.context import ContextSnapshotBuilderV2
from server.tomoko.main import TomokoProcessCore
from server.tomoko.prompt import PromptBuilderV2
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


@dataclass(slots=True)
class HotPathAudioConversation:
    vad: VADProcessor
    stt_backend: StreamingSttBackend
    tomoko_core: TomokoProcessCore
    prompt_builder: PromptBuilderV2
    prompt_executor: PromptExecutor
    speech_rms_threshold: float = 0.02
    context_builder: ContextSnapshotBuilderV2 = field(default_factory=ContextSnapshotBuilderV2)
    _audio_clock_ms: float = 0.0
    _recent_utterances: list[str] = field(default_factory=list)

    async def process_audio_bytes(self, payload: bytes) -> HotPathConversationResult | None:
        return await self.process_audio_samples(audio_bytes_to_samples(payload))

    async def process_audio_samples(
        self,
        samples: tuple[float, ...],
    ) -> HotPathConversationResult | None:
        if not samples:
            return None
        now_ms = self._audio_clock_ms
        self._audio_clock_ms += len(samples) / self.vad.sample_rate * 1000.0
        segment = self.vad.process_chunk(
            samples,
            speech_probability=speech_probability_from_rms(
                samples,
                threshold=self.speech_rms_threshold,
            ),
            now_ms=now_ms,
        )
        if segment is None:
            return None
        return await self.process_segment(segment)

    async def process_segment(self, segment: AudioSpeechSegment) -> HotPathConversationResult:
        observations = await observation_events(segment, self.stt_backend)
        final_observation = next(
            (observation for observation in observations if observation.is_final),
            None,
        )
        durable = (
            self.tomoko_core.adopt_final_observation(final_observation)
            if final_observation is not None
            else None
        )
        if durable is None:
            if final_observation is not None and final_observation.is_final:
                logger.info(
                    "blank_final_stt_ignored observation_id=%s",
                    final_observation.id,
                )
            return HotPathConversationResult(
                observations=observations,
                durable_utterance=None,
                context_snapshot=None,
                prompt_request=None,
                execution_result=PromptExecutionResult(),
            )

        self._recent_utterances.append(durable.text)
        snapshot = self.context_builder.build(
            session_id=durable.session_id,
            recent_utterances=self._recent_utterances[-8:],
            summaries=[],
            calendar_loader=lambda: {},
            user_status=None,
            candidates=[],
        )
        request = self.prompt_builder.build_main_reply(snapshot, durable.text)
        execution_result = await self.prompt_executor.execute(request)
        return HotPathConversationResult(
            observations=observations,
            durable_utterance=durable,
            context_snapshot=snapshot,
            prompt_request=request,
            execution_result=execution_result,
        )


def create_default_audio_conversation(prompt_executor: PromptExecutor) -> HotPathAudioConversation:
    return HotPathAudioConversation(
        vad=VADProcessor(),
        stt_backend=AppleSpeechStreamingBackend(),
        tomoko_core=TomokoProcessCore(SessionBoundaryModel()),
        prompt_builder=PromptBuilderV2(),
        prompt_executor=prompt_executor,
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
