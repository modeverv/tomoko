from __future__ import annotations

import asyncio
import math
import os
import tempfile
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import numpy as np

from server.edge.pipeline.stt_coreml import WhisperCoreMLSTT
from server.edge.pipeline.stt_whisperkit import WhisperKitServeSTT
from server.shared.config import BackendSpec
from server.shared.models import SpeechSegment, Transcript


class SpeechTranscriber(Protocol):
    async def transcribe(self, segment: SpeechSegment) -> Transcript: ...


class WarmableSpeechTranscriber(SpeechTranscriber, Protocol):
    async def warm_up(self) -> None: ...


class StreamingSpeechTranscriber(SpeechTranscriber, Protocol):
    async def process_stream_chunk(
        self,
        chunk: np.ndarray,
        *,
        device_id: str,
        sample_rate: int,
    ) -> Transcript | None: ...

    def reset_stream(self) -> None: ...


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

    async def warm_up(self) -> None:
        return None

    def _transcribe_text(self, audio: np.ndarray) -> str:
        segments, _info = self.model.transcribe(
            audio, 
            language=self.language,
            initial_prompt="ともこ"
        )
        return "".join(part.text.strip() for part in segments).strip()


class MlxWhisperSTT:
    def __init__(
        self,
        *,
        model_name: str = "mlx-community/whisper-small-mlx",
        language: str = "ja",
        initial_prompt: str = "ともこ",
        streaming: bool = False,
        stream_interval_ms: int = 1000,
        stream_min_audio_ms: int = 1000,
    ) -> None:
        self.model_name = model_name
        self.language = language
        self.initial_prompt = initial_prompt
        self.streaming = streaming
        self.stream_interval_ms = stream_interval_ms
        self.stream_min_audio_ms = stream_min_audio_ms
        self._stream_buffer: list[np.ndarray] = []
        self._stream_samples = 0
        self._stream_samples_since_emit = 0
        self._last_stream_text = ""

    async def transcribe(self, segment: SpeechSegment) -> Transcript:
        text = await asyncio.to_thread(self._transcribe_audio, segment.audio, 16000)
        return Transcript(
            text=text,
            device_id=segment.device_id,
            speaker=None,
            audio_level_db=_audio_level_db(segment.audio),
            recorded_at=segment.ended_at,
            is_final=True,
        )

    async def warm_up(self) -> None:
        now = datetime.now(UTC)
        segment = SpeechSegment(
            audio=np.zeros(16000, dtype=np.float32),
            started_at=now,
            ended_at=now,
            device_id="warmup",
            vad_confidence=0.0,
        )
        await self.transcribe(segment)
        self.reset_stream()

    async def process_stream_chunk(
        self,
        chunk: np.ndarray,
        *,
        device_id: str,
        sample_rate: int,
    ) -> Transcript | None:
        if not self.streaming:
            return None

        self._stream_buffer.append(chunk.astype(np.float32, copy=True))
        self._stream_samples += len(chunk)
        self._stream_samples_since_emit += len(chunk)
        min_samples = int(sample_rate * self.stream_min_audio_ms / 1000)
        interval_samples = int(sample_rate * self.stream_interval_ms / 1000)
        if self._stream_samples < min_samples:
            return None
        if self._stream_samples_since_emit < interval_samples:
            return None

        self._stream_samples_since_emit = 0
        audio = np.concatenate(self._stream_buffer)
        text = await asyncio.to_thread(self._transcribe_audio, audio, sample_rate)
        if not text or text == self._last_stream_text:
            return None
        self._last_stream_text = text
        return Transcript(
            text=text,
            device_id=device_id,
            speaker=None,
            audio_level_db=_audio_level_db(audio),
            recorded_at=datetime.now(UTC),
            is_final=False,
        )

    def reset_stream(self) -> None:
        self._stream_buffer = []
        self._stream_samples = 0
        self._stream_samples_since_emit = 0
        self._last_stream_text = ""

    def _transcribe_audio(self, audio: np.ndarray, sample_rate: int) -> str:
        import mlx_whisper

        audio_path = _write_temp_wav(audio, sample_rate)
        try:
            try:
                result = mlx_whisper.transcribe(
                    str(audio_path),
                    path_or_hf_repo=self.model_name,
                    language=self.language,
                    initial_prompt=self.initial_prompt,
                )
            except TypeError:
                result = mlx_whisper.transcribe(
                    str(audio_path),
                    path_or_hf_repo=self.model_name,
                )
        finally:
            audio_path.unlink(missing_ok=True)
        return str(result.get("text", "")).strip()


def create_stt_transcriber(spec: BackendSpec) -> SpeechTranscriber:
    if spec.type == "faster_whisper":
        return FasterWhisperSTT(model_name=spec.model or "small")
    if spec.type == "mlx_whisper":
        return MlxWhisperSTT(
            model_name=spec.model or "mlx-community/whisper-small-mlx",
            streaming=spec.streaming,
            stream_interval_ms=spec.stream_interval_ms,
            stream_min_audio_ms=spec.stream_min_audio_ms,
        )
    if spec.type == "whisper_coreml":
        model_path = spec.model_path or spec.model
        if not model_path:
            raise ValueError("whisper_coreml backend requires model_path or model")
        return WhisperCoreMLSTT(
            model_path=model_path,
            command=spec.command or "whisper-cli",
            streaming=spec.streaming,
            stream_interval_ms=spec.stream_interval_ms,
            stream_min_audio_ms=spec.stream_min_audio_ms,
        )
    if spec.type == "whisperkit_serve":
        return WhisperKitServeSTT(
            url=spec.url or "http://127.0.0.1:50060",
            model_name=spec.model or "small",
            command=spec.command or "whisperkit-cli",
            streaming=spec.streaming,
            stream_interval_ms=spec.stream_interval_ms,
            stream_min_audio_ms=spec.stream_min_audio_ms,
        )
    raise ValueError(f"unsupported STT backend type: {spec.type}")


def supports_streaming(transcriber: SpeechTranscriber | None) -> bool:
    return (
        transcriber is not None
        and hasattr(transcriber, "process_stream_chunk")
        and hasattr(transcriber, "reset_stream")
    )


async def warm_up_transcriber(transcriber: SpeechTranscriber | None) -> None:
    if transcriber is not None and hasattr(transcriber, "warm_up"):
        await transcriber.warm_up()


def _audio_level_db(audio: np.ndarray) -> float:
    if audio.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(audio.astype(np.float32, copy=False)))))
    if rms <= 0:
        return -120.0
    return 20.0 * math.log10(rms)


def _write_temp_wav(audio: np.ndarray, sample_rate: int) -> Path:
    samples = np.clip(audio.astype(np.float32, copy=False), -1.0, 1.0)
    pcm = (samples * 32767.0).astype(np.int16)
    fd, path_name = tempfile.mkstemp(prefix="tomoko-stt-", suffix=".wav")
    os.close(fd)
    path = Path(path_name)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())
    return path


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

    async def warm_up(self) -> None:
        return None
