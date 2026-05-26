from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import numpy as np

from server.edge.pipeline.stt_coreml import _audio_level_db, _write_temp_wav
from server.shared.inference.trace import trace_backend_call
from server.shared.models import SpeechSegment, Transcript

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE = ROOT / "_tools" / "apple_speech_stt" / "AppleSpeechSTT.swift"
DEFAULT_PLIST = ROOT / "_tools" / "apple_speech_stt" / "Info.plist"
DEFAULT_APP = ROOT / ".cache" / "tomoko" / "AppleSpeechSTT.app"
DEFAULT_BINARY = DEFAULT_APP / "Contents" / "MacOS" / "apple-speech-stt"


class AppleSpeechSTT:
    def __init__(
        self,
        *,
        command: str | None = None,
        source_path: str | None = None,
        plist_path: str | None = None,
        language: str = "ja-JP",
        on_device: bool = True,
        timeout_s: float = 30.0,
    ) -> None:
        self.command = command or str(DEFAULT_BINARY)
        self.source_path = Path(source_path) if source_path else DEFAULT_SOURCE
        self.plist_path = Path(plist_path) if plist_path else DEFAULT_PLIST
        self.language = language
        self.on_device = on_device
        self.timeout_s = timeout_s

    async def transcribe(self, segment: SpeechSegment) -> Transcript:
        request_id = str(uuid4())
        started_at = perf_counter()
        trace_backend_call(
            event="start",
            kind="stt",
            role="stt",
            backend="apple_speech",
            model="Speech.framework",
            request_id=request_id,
            queue_key="apple_speech",
            audio_ms=_audio_ms(segment.audio, 16000),
        )
        try:
            text = await asyncio.to_thread(self._transcribe_audio, segment.audio, 16000)
        except Exception as exc:
            trace_backend_call(
                event="error",
                kind="stt",
                role="stt",
                backend="apple_speech",
                model="Speech.framework",
                request_id=request_id,
                queue_key="apple_speech",
                total_ms=_elapsed_ms(started_at),
                error=type(exc).__name__,
            )
            raise
        trace_backend_call(
            event="done",
            kind="stt",
            role="stt",
            backend="apple_speech",
            model="Speech.framework",
            request_id=request_id,
            queue_key="apple_speech",
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

    def _transcribe_audio(self, audio: np.ndarray, sample_rate: int) -> str:
        self._ensure_command()
        audio_path = _write_temp_wav(audio, sample_rate)
        try:
            args = [
                self.command,
                "--audio",
                str(audio_path),
                "--locale",
                self.language,
                "--timeout",
                str(self.timeout_s),
            ]
            if self.on_device:
                args.append("--on-device")
            try:
                completed = subprocess.run(
                    args,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s + 5.0,
                )
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or exc.stdout or "").strip()
                message = f"Apple Speech STT failed with exit code {exc.returncode}"
                if detail:
                    message = f"{message}: {detail}"
                raise RuntimeError(message) from exc
        finally:
            audio_path.unlink(missing_ok=True)
        payload = json.loads(completed.stdout)
        return str(payload.get("text", "")).strip()

    def _ensure_command(self) -> None:
        command_path = Path(self.command)
        if command_path.exists() and command_path != DEFAULT_BINARY:
            return
        if command_path.exists() and not self._needs_rebuild(command_path):
            return
        if (
            not command_path.is_absolute()
            and command_path.parent == Path(".")
            and shutil.which(self.command) is not None
        ):
            return
        if not self.source_path.exists():
            raise RuntimeError(f"Apple Speech STT source is missing: {self.source_path}")
        if not self.plist_path.exists():
            raise RuntimeError(f"Apple Speech STT Info.plist is missing: {self.plist_path}")
        if shutil.which("swiftc") is None:
            raise RuntimeError("swiftc is required to build the Apple Speech STT sidecar")

        command_path.parent.mkdir(parents=True, exist_ok=True)
        if command_path == DEFAULT_BINARY:
            (DEFAULT_APP / "Contents").mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.plist_path, DEFAULT_APP / "Contents" / "Info.plist")
        subprocess.run(
            [
                "swiftc",
                "-O",
                str(self.source_path),
                "-Xlinker",
                "-sectcreate",
                "-Xlinker",
                "__TEXT",
                "-Xlinker",
                "__info_plist",
                "-Xlinker",
                str(self.plist_path),
                "-o",
                str(command_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        if shutil.which("codesign") is not None:
            subprocess.run(
                ["codesign", "--force", "--sign", "-", str(command_path)],
                check=True,
                capture_output=True,
                text=True,
            )

    def _needs_rebuild(self, command_path: Path) -> bool:
        binary_mtime = command_path.stat().st_mtime
        return (
            self.source_path.exists()
            and self.source_path.stat().st_mtime > binary_mtime
            or self.plist_path.exists()
            and self.plist_path.stat().st_mtime > binary_mtime
        )


def _audio_ms(audio: np.ndarray, sample_rate: int) -> float:
    if sample_rate <= 0:
        return 0.0
    return len(audio) / sample_rate * 1000.0


def _elapsed_ms(started_at: float) -> float:
    return (perf_counter() - started_at) * 1000
