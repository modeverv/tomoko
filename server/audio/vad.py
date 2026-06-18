from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

from server.shared.models import AudioSpeechSegment


def _dt_from_ms(ms: float) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


@dataclass(slots=True)
class VADProcessor:
    sample_rate: int = 16000
    speech_threshold: float = 0.5
    silence_ms: int = 400
    pre_roll_ms: int = 500
    _pre_roll: deque[tuple[float, tuple[float, ...]]] = field(default_factory=deque)
    _speech_chunks: list[tuple[float, tuple[float, ...]]] = field(default_factory=list)
    _speech_started_ms: float | None = None
    _last_speech_ms: float | None = None

    def process_chunk(
        self,
        chunk: tuple[float, ...],
        *,
        speech_probability: float,
        now_ms: float,
        recommended_silence_ms: int | None = None,
    ) -> AudioSpeechSegment | None:
        duration_ms = len(chunk) / self.sample_rate * 1000.0
        effective_silence_ms = recommended_silence_ms or self.silence_ms
        if speech_probability >= self.speech_threshold:
            if self._speech_started_ms is None:
                self._speech_started_ms = now_ms
                retained = [
                    item for item in self._pre_roll if now_ms - item[0] <= self.pre_roll_ms
                ]
                self._speech_chunks.extend(retained)
            self._speech_chunks.append((now_ms, chunk))
            self._last_speech_ms = now_ms + duration_ms
            self._trim_pre_roll(now_ms)
            return None

        if self._speech_started_ms is None:
            self._pre_roll.append((now_ms, chunk))
            self._trim_pre_roll(now_ms)
            return None

        silence_elapsed = (
            self._last_speech_ms is not None
            and now_ms - self._last_speech_ms >= effective_silence_ms
        )
        if silence_elapsed:
            return self._finish_segment(now_ms)

        self._speech_chunks.append((now_ms, chunk))
        return None

    def _trim_pre_roll(self, now_ms: float) -> None:
        while self._pre_roll and now_ms - self._pre_roll[0][0] > self.pre_roll_ms:
            self._pre_roll.popleft()

    def _finish_segment(self, ended_ms: float) -> AudioSpeechSegment:
        assert self._speech_started_ms is not None
        samples: list[float] = []
        started_ms = self._speech_chunks[0][0] if self._speech_chunks else self._speech_started_ms
        for _, chunk in self._speech_chunks:
            samples.extend(chunk)
        segment = AudioSpeechSegment(
            samples=tuple(samples),
            sample_rate=self.sample_rate,
            started_at=_dt_from_ms(started_ms),
            ended_at=_dt_from_ms(ended_ms),
        )
        self._speech_chunks.clear()
        self._speech_started_ms = None
        self._last_speech_ms = None
        self._trim_pre_roll(ended_ms)
        return segment
