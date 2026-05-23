from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime
from typing import Protocol

import numpy as np

from server.shared.config import BackendSpec
from server.shared.models import SpeechSegment, Transcript


class SpeechTranscriber(Protocol):
    async def transcribe(self, segment: SpeechSegment) -> Transcript: ...


class FasterWhisperSTT:
    def __init__(
        self,
        *,
        model_name: str = "small",
        device: str = "auto",
        compute_type: str = "auto",
        language: str = "ja",
    ) -> None:
        from faster_whisper import WhisperModel

        self.model = WhisperModel(model_name, device=device, compute_type=compute_type)
        self.language = language

    async def transcribe(self, segment: SpeechSegment) -> Transcript:
        text = await asyncio.to_thread(self._transcribe_text, segment.audio)
        return Transcript(
            text=text,
            device_id=segment.device_id,
            speaker=None,
            audio_level_db=_audio_level_db(segment.audio),
            recorded_at=segment.ended_at,
            is_final=True,
        )

    def _transcribe_text(self, audio: np.ndarray) -> str:
        segments, _info = self.model.transcribe(audio, language=self.language)
        return "".join(part.text.strip() for part in segments).strip()


def create_stt_transcriber(spec: BackendSpec) -> SpeechTranscriber:
    if spec.type != "faster_whisper":
        raise ValueError(f"unsupported STT backend type: {spec.type}")
    return FasterWhisperSTT(model_name=spec.model or "small")


def _audio_level_db(audio: np.ndarray) -> float:
    if audio.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(audio.astype(np.float32, copy=False)))))
    if rms <= 0:
        return -120.0
    return 20.0 * math.log10(rms)


class NullTranscriber:
    async def transcribe(self, segment: SpeechSegment) -> Transcript:
        return Transcript(
            text="",
            device_id=segment.device_id,
            speaker=None,
            audio_level_db=_audio_level_db(segment.audio),
            recorded_at=datetime.now(UTC),
            is_final=True,
        )
