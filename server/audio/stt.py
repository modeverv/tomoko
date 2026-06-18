from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from server.shared.models import AudioSpeechSegment, PartialTranscriptObservation


@dataclass(frozen=True, slots=True)
class StreamingSttEvent:
    text: str
    is_final: bool
    stability: float
    p_yielding: float | None = None
    recommended_silence_ms: int | None = None


class AppleSpeechStreamingBackend:
    async def transcribe_stream(
        self,
        _segment: AudioSpeechSegment,
    ) -> AsyncIterator[StreamingSttEvent]:
        raise RuntimeError("Apple Speech backend requires macOS runtime wiring")


class StaticStreamingSttBackend:
    def __init__(self, events: list[StreamingSttEvent]) -> None:
        self._events = events

    async def transcribe_stream(
        self,
        _segment: AudioSpeechSegment,
    ) -> AsyncIterator[StreamingSttEvent]:
        for event in self._events:
            yield event


async def observation_events(
    segment: AudioSpeechSegment,
    backend: AppleSpeechStreamingBackend | StaticStreamingSttBackend,
) -> list[PartialTranscriptObservation]:
    observations: list[PartialTranscriptObservation] = []
    async for event in backend.transcribe_stream(segment):
        observations.append(
            PartialTranscriptObservation(
                text=event.text,
                is_final=event.is_final,
                stability=event.stability,
                p_yielding=event.p_yielding,
                recommended_silence_ms=event.recommended_silence_ms,
                audio_started_at=segment.started_at,
                audio_ended_at=segment.ended_at,
                trace_id=segment.trace_id,
            )
        )
    return observations
