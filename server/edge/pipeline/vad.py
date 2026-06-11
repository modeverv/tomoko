from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import numpy as np

from server.shared.models import SpeechSegment


class VADScorer(Protocol):
    def process_chunk(self, chunk: np.ndarray) -> float: ...


class SileroVAD:
    def __init__(self, model: object | None = None) -> None:
        self.model = model or self._load_model()

    @staticmethod
    def _load_model() -> object:
        import torch

        model, _utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            trust_repo=True,
        )
        return model

    def process_chunk(self, chunk: np.ndarray) -> float:
        import torch

        tensor = torch.from_numpy(chunk.astype(np.float32, copy=False))
        with torch.no_grad():
            return float(self.model(tensor, 16000).item())


@dataclass
class VADResult:
    speech_probability: float
    state_changed_to: str | None = None
    segment: SpeechSegment | None = None


class VADProcessor:
    def __init__(
        self,
        vad: VADScorer,
        *,
        silence_ms: int = 400,
        sample_rate: int = 16000,
        speech_threshold: float = 0.5,
        device_id: str = "local",
    ) -> None:
        self.vad = vad
        self.default_silence_ms = silence_ms
        self.silence_ms = silence_ms
        self.sample_rate = sample_rate
        self.speech_threshold = speech_threshold
        self.device_id = device_id
        self.state = "idle"
        self._buffer: list[np.ndarray] = []
        self._silent_samples = 0
        self._started_at: datetime | None = None
        self._max_speech_probability = 0.0

    def process_chunk(self, chunk: np.ndarray) -> VADResult:
        probability = self.vad.process_chunk(chunk)

        if self.state == "processing":
            return VADResult(speech_probability=probability)

        is_speech = probability >= self.speech_threshold
        if self.state == "idle" and not is_speech:
            return VADResult(speech_probability=probability)

        if self.state == "idle":
            self.state = "listening"
            self._started_at = datetime.now(UTC)
            self._buffer = []
            self._silent_samples = 0
            self._max_speech_probability = probability
            state_changed_to = "listening"
        else:
            state_changed_to = None

        self._buffer.append(chunk.astype(np.float32, copy=False))
        if is_speech:
            self._max_speech_probability = max(self._max_speech_probability, probability)
            self._silent_samples = 0
            return VADResult(
                speech_probability=probability,
                state_changed_to=state_changed_to,
            )

        self._silent_samples += len(chunk)
        silence_ms = self._silent_samples * 1000 / self.sample_rate
        if silence_ms < self.silence_ms:
            return VADResult(
                speech_probability=probability,
                state_changed_to=state_changed_to,
            )

        segment = SpeechSegment(
            audio=np.concatenate(self._buffer),
            started_at=self._started_at or datetime.now(UTC),
            ended_at=datetime.now(UTC),
            device_id=self.device_id,
            vad_confidence=self._max_speech_probability,
        )
        self.state = "processing"
        return VADResult(
            speech_probability=probability,
            state_changed_to="processing",
            segment=segment,
        )

    def reset(self) -> None:
        self.state = "idle"
        self._buffer = []
        self._silent_samples = 0
        self._started_at = None
        self._max_speech_probability = 0.0
        self.silence_ms = self.default_silence_ms


def create_vad_processor(silence_ms: int = 400) -> VADProcessor:
    return VADProcessor(vad=SileroVAD(), silence_ms=silence_ms)
