from __future__ import annotations

import asyncio
import math
import os
import re
import shutil
import subprocess
import tempfile
import wave
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import numpy as np

from server.shared.inference.trace import trace_backend_call
from server.shared.models import SpeechSegment, Transcript


class WhisperCoreMLSTT:
    def __init__(
        self,
        *,
        model_path: str,
        command: str = "whisper-cli",
        language: str = "ja",
        initial_prompt: str = "ともこ",
        streaming: bool = False,
        stream_interval_ms: int = 1000,
        stream_min_audio_ms: int = 1000,
    ) -> None:
        self.model_path = model_path
        self.command = command
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
        request_id = str(uuid4())
        started_at = perf_counter()
        trace_backend_call(
            event="start",
            kind="stt",
            role="stt",
            backend="whisper_coreml",
            model=self.model_path,
            request_id=request_id,
            queue_key="local_coreml",
            audio_ms=_audio_ms(segment.audio, 16000),
        )
        try:
            text = await asyncio.to_thread(self._transcribe_audio, segment.audio, 16000)
        except Exception as exc:
            trace_backend_call(
                event="error",
                kind="stt",
                role="stt",
                backend="whisper_coreml",
                model=self.model_path,
                request_id=request_id,
                queue_key="local_coreml",
                total_ms=_elapsed_ms(started_at),
                error=type(exc).__name__,
            )
            raise
        trace_backend_call(
            event="done",
            kind="stt",
            role="stt",
            backend="whisper_coreml",
            model=self.model_path,
            request_id=request_id,
            queue_key="local_coreml",
            total_ms=_elapsed_ms(started_at),
            text_len=len(text),
        )
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
        if shutil.which(self.command) is None:
            raise RuntimeError(
                f"{self.command!r} is not available. Install whisperkit-cli or build "
                "whisper.cpp with CoreML support, then set backends.<name>.command."
            )

        audio_path = _write_temp_wav(audio, sample_rate)
        try:
            args = self._command_args(audio_path)
            completed = subprocess.run(
                args,
                check=True,
                capture_output=True,
                text=True,
            )
        finally:
            audio_path.unlink(missing_ok=True)
        return _clean_whisper_cpp_output(completed.stdout or completed.stderr)

    def _command_args(self, audio_path: Path) -> list[str]:
        command_name = Path(self.command).name
        if command_name == "whisperkit-cli":
            args = [
                self.command,
                "transcribe",
                "--audio-path",
                str(audio_path),
                "--language",
                self.language,
                "--prompt",
                self.initial_prompt,
                "--without-timestamps",
            ]
            if self.model_path:
                option = "--model-path" if "/" in self.model_path else "--model"
                args.extend([option, self.model_path])
            return args

        return [
            self.command,
            "-m",
            self.model_path,
            "-f",
            str(audio_path),
            "-l",
            self.language,
            "--prompt",
            self.initial_prompt,
            "-nt",
            "-np",
        ]


def _audio_level_db(audio: np.ndarray) -> float:
    if audio.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(audio.astype(np.float32, copy=False)))))
    if rms <= 0:
        return -120.0
    return 20.0 * math.log10(rms)


def _audio_ms(audio: np.ndarray, sample_rate: int) -> float:
    if sample_rate <= 0:
        return 0.0
    return len(audio) / sample_rate * 1000.0


def _elapsed_ms(started_at: float) -> float:
    return (perf_counter() - started_at) * 1000


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


def _clean_whisper_cpp_output(output: str) -> str:
    lines: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("whisper_") or line.startswith("main:"):
            continue
        line = re.sub(r"^\[[^\]]+\]\s*", "", line).strip()
        if line:
            lines.append(line)
    return " ".join(lines).strip()
