from __future__ import annotations

from dataclasses import dataclass, field

from server.hot_path.model_executor import TtsBackend, is_complete_wav_chunk
from server.shared.models import (
    AudioChunkOut,
    CancelPolicy,
    PromptRequest,
    PromptScope,
    SpeechOrder,
    SpeechOrderMode,
)


@dataclass(slots=True)
class SpeechOrderExecutionResult:
    order: SpeechOrder
    audio_chunks: list[AudioChunkOut] = field(default_factory=list)
    queued: bool = False
    stopped: bool = False
    discarded_chunks: int = 0


@dataclass(slots=True)
class SpeechOrderExecutor:
    tts_backend: TtsBackend
    current_order: SpeechOrder | None = None
    current_score: float = 0.0
    append_queue: list[SpeechOrder] = field(default_factory=list)
    current_generation: int = 0
    protect_inflight_replace: bool = False

    async def execute(self, order: SpeechOrder) -> SpeechOrderExecutionResult:
        _console_event(
            "speech_order_received",
            order_id=str(order.id),
            mode=order.mode.value,
            priority=order.priority,
        )
        if order.mode == SpeechOrderMode.STOP:
            self.stop_playback(reason=f"speech_order:{order.id}")
            return SpeechOrderExecutionResult(order=order, stopped=True)

        if order.mode == SpeechOrderMode.APPEND_AFTER_CURRENT and self.current_order is not None:
            self.append_queue.append(order)
            _console_event(
                "speech_order_queued",
                order_id=str(order.id),
                queue_size=len(self.append_queue),
            )
            return SpeechOrderExecutionResult(order=order, queued=True)

        if (
            order.mode == SpeechOrderMode.REPLACE_CURRENT
            and self.protect_inflight_replace
            and self.current_order is not None
        ):
            _console_event(
                "speech_order_replace_deferred",
                order_id=str(order.id),
                current_order_id=str(self.current_order.id),
            )
            return SpeechOrderExecutionResult(order=order, queued=True)

        if order.mode == SpeechOrderMode.REPLACE_CURRENT:
            self.replace_generation()
            self.append_queue.clear()

        return await self._synthesize_current(order)

    def begin_external_playback(self, order: SpeechOrder, *, score: float) -> None:
        self.current_order = order
        self.current_score = score

    def replace_generation(self) -> int:
        self.current_generation += 1
        return self.current_generation

    def stop_playback(self, *, reason: str = "stop") -> None:
        self.replace_generation()
        self.current_order = None
        self.current_score = 0.0
        self.append_queue.clear()
        _console_event("speech_order_stopped", reason=reason)

    def is_current_generation(self, generation: int) -> bool:
        return generation == self.current_generation

    async def _synthesize_current(self, order: SpeechOrder) -> SpeechOrderExecutionResult:
        generation = self.current_generation
        self.current_order = order
        request = prompt_request_for_order(order)
        chunks: list[AudioChunkOut] = []
        discarded = 0
        async for chunk in self.tts_backend.synthesize_chunks(request, order.text):
            if not self.is_current_generation(generation):
                discarded += 1
                continue
            if not is_complete_wav_chunk(chunk):
                raise ValueError("TTS backend must yield complete WAV chunks")
            chunks.append(
                AudioChunkOut(
                    request_id=order.id,
                    chunk=chunk,
                    sample_rate=16000,
                    trace_id=order.trace_id,
                )
            )
        if chunks:
            chunks[-1].is_final = True
        if self.is_current_generation(generation) and self.current_order == order:
            self.current_order = None
            self.current_score = 0.0
        _console_event(
            "speech_order_audio_ready",
            order_id=str(order.id),
            chunks=len(chunks),
            discarded=discarded,
        )
        return SpeechOrderExecutionResult(
            order=order,
            audio_chunks=chunks,
            discarded_chunks=discarded,
        )


def prompt_request_for_order(order: SpeechOrder) -> PromptRequest:
    return PromptRequest(
        prompt_text=order.text,
        scope=PromptScope.MAIN,
        decision_id=order.scheduler_decision_id,
        utterance_id=None,
        candidate_id=None,
        priority=order.priority,
        cancel_policy=CancelPolicy.KEEP_UNTIL_COMPLETE,
        id=order.id,
        trace_id=order.trace_id,
    )


def _console_event(event: str, **fields: object) -> None:
    parts = [f"[tomoko:speech-executor] {event}"]
    for key, value in fields.items():
        text = str(value)
        if len(text) > 120:
            text = text[:117] + "..."
        parts.append(f"{key}={text!r}")
    print(" ".join(parts), flush=True)
