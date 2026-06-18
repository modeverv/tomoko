from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from server.shared.models import AudioChunkOut, ModelOutputEvent, PromptRequest


class ChatBackend:
    async def stream(self, request: PromptRequest) -> AsyncIterator[str]:
        raise NotImplementedError


class TtsBackend:
    async def synthesize_chunks(self, request: PromptRequest, text: str) -> AsyncIterator[bytes]:
        raise NotImplementedError


class StaticChatBackend(ChatBackend):
    def __init__(self, deltas: list[str]) -> None:
        self._deltas = deltas

    async def stream(self, request: PromptRequest) -> AsyncIterator[str]:
        for delta in self._deltas:
            yield delta


class StaticWavTtsBackend(TtsBackend):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def synthesize_chunks(self, request: PromptRequest, text: str) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


def is_complete_wav_chunk(chunk: bytes) -> bool:
    return len(chunk) >= 12 and chunk[:4] == b"RIFF" and chunk[8:12] == b"WAVE"


@dataclass(slots=True)
class PromptExecutionResult:
    model_events: list[ModelOutputEvent] = field(default_factory=list)
    audio_chunks: list[AudioChunkOut] = field(default_factory=list)


class PromptExecutor:
    def __init__(self, chat_backend: ChatBackend, tts_backend: TtsBackend) -> None:
        self._chat_backend = chat_backend
        self._tts_backend = tts_backend

    async def execute(self, request: PromptRequest) -> PromptExecutionResult:
        result = PromptExecutionResult()
        text_parts: list[str] = []
        async for delta in self._chat_backend.stream(request):
            text_parts.append(delta)
            result.model_events.append(
                ModelOutputEvent(
                    request_id=request.id,
                    event_kind="delta",
                    text_delta=delta,
                    trace_id=request.trace_id,
                )
            )
        full_text = "".join(text_parts)
        result.model_events.append(
            ModelOutputEvent(
                request_id=request.id,
                event_kind="complete",
                text=full_text,
                trace_id=request.trace_id,
            )
        )
        async for chunk in self._tts_backend.synthesize_chunks(request, full_text):
            if not is_complete_wav_chunk(chunk):
                raise ValueError("TTS backend must yield complete WAV chunks")
            result.audio_chunks.append(
                AudioChunkOut(
                    request_id=request.id,
                    chunk=chunk,
                    sample_rate=16000,
                    trace_id=request.trace_id,
                )
            )
        if result.audio_chunks:
            result.audio_chunks[-1].is_final = True
        return result
