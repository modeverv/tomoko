from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import wave
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from server.shared.models import AudioSpeechSegment, PartialTranscriptObservation

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "scripts" / "apple_speech_stt" / "AppleSpeechSTT.swift"
DEFAULT_PLIST = ROOT / "scripts" / "apple_speech_stt" / "Info.plist"
DEFAULT_APP = ROOT / ".cache" / "tomoko" / "AppleSpeechSTT.app"
DEFAULT_BINARY = DEFAULT_APP / "Contents" / "MacOS" / "apple-speech-stt"
NO_SPEECH_ERROR = "No speech detected"
DEFAULT_CONTEXTUAL_STRINGS = (
    "ともこ",
    "トモコ",
    "Tomoko",
    "智子",
    "朋子",
    "tomoko",
)


@dataclass(frozen=True, slots=True)
class StreamingSttEvent:
    text: str
    is_final: bool
    stability: float
    p_yielding: float | None = None
    recommended_silence_ms: int | None = None


class AppleSpeechStreamingBackend:
    def __init__(
        self,
        *,
        command: str | None = None,
        source_path: str | None = None,
        plist_path: str | None = None,
        language: str = "ja-JP",
        on_device: bool = True,
        contextual_strings: tuple[str, ...] = DEFAULT_CONTEXTUAL_STRINGS,
        timeout_s: float = 30.0,
    ) -> None:
        self.command = command or str(DEFAULT_BINARY)
        self.source_path = Path(source_path) if source_path else DEFAULT_SOURCE
        self.plist_path = Path(plist_path) if plist_path else DEFAULT_PLIST
        self.language = language
        self.on_device = on_device
        self.contextual_strings = contextual_strings
        self.timeout_s = timeout_s

    async def transcribe_stream(
        self,
        segment: AudioSpeechSegment,
    ) -> AsyncIterator[StreamingSttEvent]:
        text = await asyncio.to_thread(self._transcribe_audio, segment)
        yield StreamingSttEvent(text=text, is_final=True, stability=1.0)

    async def warm_up(self) -> None:
        await asyncio.to_thread(self._ensure_command)

    def _transcribe_audio(self, segment: AudioSpeechSegment) -> str:
        self._ensure_command()
        audio_path = write_segment_wav(segment)
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
            for contextual_string in self.contextual_strings:
                args.extend(["--contextual-string", contextual_string])
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
                if _is_no_speech_error(detail):
                    return ""
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


def write_segment_wav(segment: AudioSpeechSegment) -> Path:
    fd, path_name = tempfile.mkstemp(prefix="tomoko-v2-stt-", suffix=".wav")
    os.close(fd)
    path = Path(path_name)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(segment.sample_rate)
        wav.writeframes(_float_samples_to_pcm16(segment.samples))
    return path


def apple_speech_runtime_available() -> dict[str, bool]:
    binary = DEFAULT_BINARY.exists()
    return {
        "binary": binary,
        "source": DEFAULT_SOURCE.exists(),
        "plist": DEFAULT_PLIST.exists(),
        "swiftc": shutil.which("swiftc") is not None,
    }


def _float_samples_to_pcm16(samples: tuple[float, ...]) -> bytes:
    import array

    clipped = [max(-1.0, min(1.0, sample)) for sample in samples]
    pcm = array.array("h", (int(sample * 32767.0) for sample in clipped))
    return pcm.tobytes()


def _is_no_speech_error(detail: str) -> bool:
    if not detail:
        return False
    try:
        payload = json.loads(detail)
    except json.JSONDecodeError:
        return NO_SPEECH_ERROR in detail
    return str(payload.get("error", "")).strip() == NO_SPEECH_ERROR
