from __future__ import annotations

import json
import math
import re
import time
import wave
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from server.edge.pipeline.stt import SpeechTranscriber
from server.shared.models import SpeechSegment


@dataclass(frozen=True, slots=True)
class DebugRecordingResult:
    recording_id: str
    kind: str
    wav_path: Path
    metadata_path: Path
    duration_ms: float
    sample_count: int
    rms_db: float
    peak_db: float
    transcript: str | None
    stt_elapsed_ms: float | None
    expected_text: str | None

    def to_event(self) -> dict[str, object]:
        return {
            "type": "debug_recording_saved",
            "recording_id": self.recording_id,
            "kind": self.kind,
            "wav_path": str(self.wav_path),
            "metadata_path": str(self.metadata_path),
            "duration_ms": round(self.duration_ms, 1),
            "sample_count": self.sample_count,
            "rms_db": round(self.rms_db, 1),
            "peak_db": round(self.peak_db, 1),
            "transcript": self.transcript,
            "stt_elapsed_ms": (
                round(self.stt_elapsed_ms, 1) if self.stt_elapsed_ms is not None else None
            ),
            "expected_text": self.expected_text,
        }


class DebugAudioRecorder:
    def __init__(
        self,
        *,
        root: Path,
        transcriber: SpeechTranscriber | None,
        sample_rate: int = 16000,
    ) -> None:
        self.root = root
        self.transcriber = transcriber
        self.sample_rate = sample_rate
        self.recording_id: str | None = None
        self.kind = "noise"
        self.expected_text: str | None = None
        self.started_at: datetime | None = None
        self.max_samples: int | None = None
        self._chunks: list[np.ndarray] = []
        self._sample_count = 0

    @property
    def is_recording(self) -> bool:
        return self.recording_id is not None

    def start(
        self,
        *,
        kind: str,
        duration_ms: int | None = None,
        expected_text: str | None = None,
    ) -> dict[str, object]:
        if self.is_recording:
            raise ValueError("debug recording is already active")
        self.recording_id = _recording_id(kind)
        self.kind = _safe_kind(kind)
        self.expected_text = expected_text
        self.started_at = datetime.now(UTC)
        self.max_samples = (
            int(self.sample_rate * duration_ms / 1000)
            if duration_ms is not None and duration_ms > 0
            else None
        )
        self._chunks = []
        self._sample_count = 0
        return {
            "type": "debug_recording_started",
            "recording_id": self.recording_id,
            "kind": self.kind,
            "duration_ms": duration_ms,
            "expected_text": expected_text,
        }

    def add_chunk(self, chunk_bytes: bytes) -> bool:
        if not self.is_recording:
            return False
        chunk = np.frombuffer(chunk_bytes, dtype=np.float32).astype(np.float32, copy=True)
        if self.max_samples is not None:
            remaining = self.max_samples - self._sample_count
            if remaining <= 0:
                return True
            chunk = chunk[:remaining]
        self._chunks.append(chunk)
        self._sample_count += len(chunk)
        return self.max_samples is not None and self._sample_count >= self.max_samples

    async def stop(self) -> DebugRecordingResult:
        if not self.is_recording or self.recording_id is None:
            raise ValueError("debug recording is not active")
        audio = (
            np.concatenate(self._chunks)
            if self._chunks
            else np.zeros(0, dtype=np.float32)
        )
        recording_id = self.recording_id
        kind = self.kind
        expected_text = self.expected_text
        started_at = self.started_at or datetime.now(UTC)

        self.recording_id = None
        self._chunks = []
        self._sample_count = 0

        out_dir = self.root / "audio-recordings"
        out_dir.mkdir(parents=True, exist_ok=True)
        wav_path = out_dir / f"{recording_id}.wav"
        metadata_path = out_dir / f"{recording_id}.json"
        _write_wav(wav_path, audio, self.sample_rate)

        transcript: str | None = None
        stt_elapsed_ms: float | None = None
        if kind == "read_aloud" and self.transcriber is not None:
            segment = SpeechSegment(
                audio=audio,
                started_at=started_at,
                ended_at=datetime.now(UTC),
                device_id="debug",
                vad_confidence=1.0,
            )
            start = time.perf_counter()
            stt_result = await self.transcriber.transcribe(segment)
            stt_elapsed_ms = (time.perf_counter() - start) * 1000
            transcript = stt_result.text

        result = DebugRecordingResult(
            recording_id=recording_id,
            kind=kind,
            wav_path=wav_path,
            metadata_path=metadata_path,
            duration_ms=(len(audio) * 1000 / self.sample_rate),
            sample_count=len(audio),
            rms_db=_level_db(audio),
            peak_db=_peak_db(audio),
            transcript=transcript,
            stt_elapsed_ms=stt_elapsed_ms,
            expected_text=expected_text,
        )
        metadata_path.write_text(
            json.dumps(result.to_event(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return result


def _recording_id(kind: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{_safe_kind(kind)}"


def _safe_kind(kind: str) -> str:
    value = re.sub(r"[^a-z0-9_-]+", "-", kind.lower()).strip("-")
    return value or "debug"


def _write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    samples = np.clip(audio.astype(np.float32, copy=False), -1.0, 1.0)
    pcm = (samples * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def _level_db(audio: np.ndarray) -> float:
    if audio.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(audio.astype(np.float32, copy=False)))))
    if rms <= 0:
        return -120.0
    return 20.0 * math.log10(rms)


def _peak_db(audio: np.ndarray) -> float:
    if audio.size == 0:
        return -120.0
    peak = float(np.max(np.abs(audio.astype(np.float32, copy=False))))
    if peak <= 0:
        return -120.0
    return 20.0 * math.log10(peak)
