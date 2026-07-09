from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import wave
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from server.shared.models import AudioSpeechSegment, PartialTranscriptObservation

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "scripts" / "apple_speech_stt" / "AppleSpeechSTT.swift"
DEFAULT_PLIST = ROOT / "scripts" / "apple_speech_stt" / "Info.plist"
DEFAULT_APP = ROOT / ".cache" / "tomoko" / "AppleSpeechSTT.app"
DEFAULT_BINARY = DEFAULT_APP / "Contents" / "MacOS" / "apple-speech-stt"
WHISPERKIT_LARGE_V3_TURBO_MODEL = "large-v3-v20240930_turbo"
WHISPERKIT_DEFAULT_LANGUAGE = "ja"
WHISPERKIT_DEFAULT_MODEL_PREFIX = "openai"
WHISPERKIT_DEFAULT_COMPUTE_UNITS = "cpuAndNeuralEngine"
NO_SPEECH_ERROR = "No speech detected"
DEFAULT_CONTEXTUAL_STRINGS = (
    "ともこ",
    "トモコ",
    "Tomoko",
    "智子",
    "朋子",
    "tomoko",
    "予定",
    "会議",
    "今週",
    "今日",
    "明日",
    "天気",
    "昼ごはん",
    "優先順位",
    "空き時間",
    "締め切り",
    "リマインド",
)


@dataclass(frozen=True, slots=True)
class StreamingSttEvent:
    text: str
    is_final: bool
    stability: float
    p_yielding: float | None = None
    recommended_silence_ms: int | None = None


class TranscribesAudio(Protocol):
    async def transcribe_stream(
        self,
        segment: AudioSpeechSegment,
    ) -> AsyncIterator[StreamingSttEvent]: ...


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
        streaming: bool = True,
        stream_interval_ms: int = 400,
        stream_min_audio_ms: int = 1000,
        stream_sidecar: bool | None = None,
    ) -> None:
        self.command = command or str(DEFAULT_BINARY)
        self.source_path = Path(source_path) if source_path else DEFAULT_SOURCE
        self.plist_path = Path(plist_path) if plist_path else DEFAULT_PLIST
        self.language = language
        self.on_device = on_device
        self.contextual_strings = contextual_strings
        self.timeout_s = timeout_s
        self.streaming = streaming
        self.stream_interval_ms = stream_interval_ms
        self.stream_min_audio_ms = stream_min_audio_ms
        self.stream_sidecar = (
            os.environ.get("TOMOKO_V2_STT_SIDECAR_STREAM", "1") != "0"
            if stream_sidecar is None
            else stream_sidecar
        )
        self._stream_buffer: list[tuple[float, ...]] = []
        self._stream_samples = 0
        self._stream_samples_since_emit = 0
        self._stream_started_at_ms: float | None = None
        self._last_stream_text = ""
        self._sidecar_proc: asyncio.subprocess.Process | None = None
        self._sidecar_reader: asyncio.Task[None] | None = None
        self._sidecar_latest_text = ""
        self._sidecar_failed = False

    async def transcribe_stream(
        self,
        segment: AudioSpeechSegment,
    ) -> AsyncIterator[StreamingSttEvent]:
        _console_event("apple_speech_start", samples=len(segment.samples), rate=segment.sample_rate)
        text = await asyncio.to_thread(self._transcribe_audio, segment)
        _console_event("apple_speech_done", text=text)
        yield StreamingSttEvent(text=text, is_final=True, stability=1.0)

    async def process_stream_chunk(
        self,
        chunk: tuple[float, ...],
        *,
        sample_rate: int,
        started_at_ms: float,
    ) -> StreamingSttEvent | None:
        if not self.streaming:
            return None
        if not chunk:
            return None
        if self._stream_started_at_ms is None:
            self._stream_started_at_ms = started_at_ms
        self._stream_buffer.append(tuple(chunk))
        self._stream_samples += len(chunk)
        self._stream_samples_since_emit += len(chunk)
        if self.stream_sidecar and not self._sidecar_failed:
            event = await self._process_sidecar_chunk(chunk, sample_rate=sample_rate)
            if not self._sidecar_failed:
                return event
        min_samples = int(sample_rate * self.stream_min_audio_ms / 1000)
        interval_samples = int(sample_rate * self.stream_interval_ms / 1000)
        if self._stream_samples < min_samples:
            return None
        if self._stream_samples_since_emit < interval_samples:
            return None

        self._stream_samples_since_emit = 0
        samples = tuple(sample for buffered in self._stream_buffer for sample in buffered)
        started_at = _datetime_from_ms(self._stream_started_at_ms)
        segment = AudioSpeechSegment(
            samples=samples,
            sample_rate=sample_rate,
            started_at=started_at,
            ended_at=started_at + timedelta(milliseconds=len(samples) / sample_rate * 1000.0),
        )
        _console_event("apple_speech_partial_start", samples=len(segment.samples), rate=sample_rate)
        text = await asyncio.to_thread(self._transcribe_audio, segment)
        _console_event("apple_speech_partial_done", text=text)
        if not text or text == self._last_stream_text:
            return None
        self._last_stream_text = text
        return StreamingSttEvent(text=text, is_final=False, stability=0.85)

    async def _process_sidecar_chunk(
        self,
        chunk: tuple[float, ...],
        *,
        sample_rate: int,
    ) -> StreamingSttEvent | None:
        try:
            proc = await self._ensure_sidecar(sample_rate)
            assert proc.stdin is not None
            proc.stdin.write(_float_samples_to_pcm16(chunk))
            await proc.stdin.drain()
        except Exception as exc:
            _console_event(
                "apple_speech_sidecar_failed",
                error=type(exc).__name__,
                message=str(exc),
            )
            self._sidecar_failed = True
            self._close_sidecar()
            return None
        text = self._sidecar_latest_text
        if not text or text == self._last_stream_text:
            return None
        self._last_stream_text = text
        _console_event("apple_speech_sidecar_partial", text=text)
        return StreamingSttEvent(text=text, is_final=False, stability=0.85)

    async def _ensure_sidecar(self, sample_rate: int) -> asyncio.subprocess.Process:
        proc = self._sidecar_proc
        if proc is not None and proc.returncode is None:
            return proc
        await asyncio.to_thread(self._ensure_command)
        args = [
            self.command,
            "--stream",
            "--rate",
            str(sample_rate),
            "--locale",
            self.language,
            "--timeout",
            str(max(self.timeout_s, 60.0)),
        ]
        for contextual_string in self.contextual_strings:
            args.extend(["--contextual-string", contextual_string])
        if self.on_device:
            args.append("--on-device")
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._sidecar_proc = proc
        self._sidecar_latest_text = ""
        self._sidecar_reader = asyncio.create_task(self._read_sidecar_lines(proc))
        _console_event("apple_speech_sidecar_started", pid=proc.pid, rate=sample_rate)
        return proc

    async def _read_sidecar_lines(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    return
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = str(payload.get("text", "")).strip()
                if text:
                    self._sidecar_latest_text = text
        except asyncio.CancelledError:
            return

    def _close_sidecar(self) -> None:
        proc = self._sidecar_proc
        self._sidecar_proc = None
        reader = self._sidecar_reader
        self._sidecar_reader = None
        self._sidecar_latest_text = ""
        if reader is not None:
            reader.cancel()
        if proc is not None and proc.returncode is None:
            if proc.stdin is not None:
                with suppress(Exception):
                    proc.stdin.close()
            with suppress(Exception):
                proc.kill()

    def reset_stream(self) -> None:
        self._stream_buffer = []
        self._stream_samples = 0
        self._stream_samples_since_emit = 0
        self._stream_started_at_ms = None
        self._last_stream_text = ""
        self._sidecar_failed = False
        self._close_sidecar()

    async def warm_up(self) -> None:
        await asyncio.to_thread(self._ensure_command)
        if not self.stream_sidecar:
            return
        # 起動直後の初回発話で partial が出ない対策: 短い無音セッションを流して
        # OS 側の on-device 認識モデルをロードさせておく。
        try:
            proc = await self._ensure_sidecar(16000)
            assert proc.stdin is not None
            proc.stdin.write(b"\x00" * 9600)
            await proc.stdin.drain()
            await asyncio.sleep(1.0)
            _console_event("apple_speech_warmup_done")
        except Exception as exc:
            _console_event(
                "apple_speech_warmup_failed",
                error=type(exc).__name__,
                message=str(exc),
            )
        finally:
            self._close_sidecar()

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


class ArgmaxWhisperKitStreamingBackend:
    def __init__(
        self,
        *,
        command: str | None = None,
        model: str = WHISPERKIT_LARGE_V3_TURBO_MODEL,
        model_path: str | None = None,
        model_prefix: str = WHISPERKIT_DEFAULT_MODEL_PREFIX,
        language: str = WHISPERKIT_DEFAULT_LANGUAGE,
        prompt: str | None = None,
        timeout_s: float = 60.0,
        streaming: bool = True,
        stream_interval_ms: int = 1000,
        stream_min_audio_ms: int = 1000,
        audio_encoder_compute_units: str = WHISPERKIT_DEFAULT_COMPUTE_UNITS,
        text_decoder_compute_units: str = WHISPERKIT_DEFAULT_COMPUTE_UNITS,
    ) -> None:
        self.command = command or _default_whisperkit_command()
        self.model = os.environ.get("TOMOKO_V2_WHISPERKIT_MODEL", model)
        self.model_path = model_path or os.environ.get("TOMOKO_V2_WHISPERKIT_MODEL_PATH") or None
        self.model_prefix = model_prefix
        self.language = language
        self.prompt = prompt or os.environ.get("TOMOKO_V2_WHISPERKIT_PROMPT") or None
        self.timeout_s = timeout_s
        self.streaming = streaming
        self.stream_interval_ms = stream_interval_ms
        self.stream_min_audio_ms = stream_min_audio_ms
        self.audio_encoder_compute_units = os.environ.get(
            "TOMOKO_V2_WHISPERKIT_AUDIO_ENCODER_COMPUTE_UNITS",
            audio_encoder_compute_units,
        )
        self.text_decoder_compute_units = os.environ.get(
            "TOMOKO_V2_WHISPERKIT_TEXT_DECODER_COMPUTE_UNITS",
            text_decoder_compute_units,
        )
        self._stream_buffer: list[tuple[float, ...]] = []
        self._stream_samples = 0
        self._stream_samples_since_emit = 0
        self._stream_started_at_ms: float | None = None
        self._last_stream_text = ""
        self._partial_failed = False

    async def transcribe_stream(
        self,
        segment: AudioSpeechSegment,
    ) -> AsyncIterator[StreamingSttEvent]:
        _console_event(
            "whisperkit_start",
            samples=len(segment.samples),
            rate=segment.sample_rate,
            model=self.model_path or self.model,
        )
        text = await asyncio.to_thread(
            self._transcribe_audio,
            segment,
            stream_simulated=False,
        )
        _console_event("whisperkit_done", text=text)
        yield StreamingSttEvent(text=text, is_final=True, stability=1.0)

    async def process_stream_chunk(
        self,
        chunk: tuple[float, ...],
        *,
        sample_rate: int,
        started_at_ms: float,
    ) -> StreamingSttEvent | None:
        if not self.streaming or not chunk or self._partial_failed:
            return None
        if self._stream_started_at_ms is None:
            self._stream_started_at_ms = started_at_ms
        self._stream_buffer.append(tuple(chunk))
        self._stream_samples += len(chunk)
        self._stream_samples_since_emit += len(chunk)
        min_samples = int(sample_rate * self.stream_min_audio_ms / 1000)
        interval_samples = int(sample_rate * self.stream_interval_ms / 1000)
        if self._stream_samples < min_samples:
            return None
        if self._stream_samples_since_emit < interval_samples:
            return None

        self._stream_samples_since_emit = 0
        samples = tuple(sample for buffered in self._stream_buffer for sample in buffered)
        started_at = _datetime_from_ms(self._stream_started_at_ms)
        segment = AudioSpeechSegment(
            samples=samples,
            sample_rate=sample_rate,
            started_at=started_at,
            ended_at=started_at + timedelta(milliseconds=len(samples) / sample_rate * 1000.0),
        )
        _console_event("whisperkit_partial_start", samples=len(segment.samples), rate=sample_rate)
        try:
            text = await asyncio.to_thread(
                self._transcribe_audio,
                segment,
                stream_simulated=True,
            )
        except Exception as exc:
            self._partial_failed = True
            _console_event(
                "whisperkit_partial_failed",
                error=type(exc).__name__,
                message=str(exc),
            )
            return None
        _console_event("whisperkit_partial_done", text=text)
        if not text or text == self._last_stream_text:
            return None
        self._last_stream_text = text
        return StreamingSttEvent(text=text, is_final=False, stability=0.82)

    def reset_stream(self) -> None:
        self._stream_buffer = []
        self._stream_samples = 0
        self._stream_samples_since_emit = 0
        self._stream_started_at_ms = None
        self._last_stream_text = ""
        self._partial_failed = False

    async def warm_up(self) -> None:
        await asyncio.to_thread(self._ensure_command)
        _console_event(
            "whisperkit_ready",
            command=self.command,
            model=self.model_path or self.model,
            audio_encoder_compute_units=self.audio_encoder_compute_units,
            text_decoder_compute_units=self.text_decoder_compute_units,
        )

    def _transcribe_audio(self, segment: AudioSpeechSegment, *, stream_simulated: bool) -> str:
        self._ensure_command()
        audio_path = write_segment_wav(segment)
        try:
            args = self._transcribe_args(audio_path, stream_simulated=stream_simulated)
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
                mode = "stream-simulated" if stream_simulated else "final"
                message = f"WhisperKit STT failed during {mode} with exit code {exc.returncode}"
                if detail:
                    message = f"{message}: {detail}"
                raise RuntimeError(message) from exc
        finally:
            audio_path.unlink(missing_ok=True)
        return _extract_whisperkit_text(completed.stdout)

    def _transcribe_args(self, audio_path: Path, *, stream_simulated: bool) -> list[str]:
        args = [
            self.command,
            "transcribe",
            "--audio-path",
            str(audio_path),
        ]
        if self.model_path:
            args.extend(["--model-path", self.model_path])
        else:
            args.extend(["--model", self.model])
            if self.model_prefix:
                args.extend(["--model-prefix", self.model_prefix])
        args.extend(
            [
                "--language",
                self.language,
                "--audio-encoder-compute-units",
                self.audio_encoder_compute_units,
                "--text-decoder-compute-units",
                self.text_decoder_compute_units,
                "--temperature",
                "0.0",
                "--without-timestamps",
                "--skip-special-tokens",
            ]
        )
        if self.prompt:
            args.extend(["--prompt", self.prompt])
        if stream_simulated:
            args.append("--stream-simulated")
        return args

    def _ensure_command(self) -> None:
        command_path = Path(self.command)
        if command_path.exists():
            return
        if shutil.which(self.command) is not None:
            return
        raise RuntimeError(f"WhisperKit/Argmax CLI is missing: {self.command}")


class StaticStreamingSttBackend:
    def __init__(self, events: list[StreamingSttEvent]) -> None:
        self._events = events

    async def transcribe_stream(
        self,
        _segment: AudioSpeechSegment,
    ) -> AsyncIterator[StreamingSttEvent]:
        for event in self._events:
            yield event

    async def process_stream_chunk(
        self,
        _chunk: tuple[float, ...],
        *,
        sample_rate: int,
        started_at_ms: float,
    ) -> StreamingSttEvent | None:
        del sample_rate, started_at_ms
        for index, event in enumerate(self._events):
            if not event.is_final:
                return self._events.pop(index)
        return None

    def reset_stream(self) -> None:
        return None


class ScriptedStreamingSttBackend:
    """Fake STT that replays scripted utterances one VAD segment at a time.

    Each utterance is a list of events: partials pop one per processed audio
    chunk while that utterance is current, and its finals are yielded when the
    segment closes, after which the next utterance becomes current.
    """

    def __init__(self, utterances: list[list[StreamingSttEvent]]) -> None:
        self._utterances = [list(events) for events in utterances]
        self._index = 0

    async def transcribe_stream(
        self,
        _segment: AudioSpeechSegment,
    ) -> AsyncIterator[StreamingSttEvent]:
        if self._index >= len(self._utterances):
            return
        events = self._utterances[self._index]
        self._index += 1
        for event in events:
            if event.is_final:
                yield event

    async def process_stream_chunk(
        self,
        _chunk: tuple[float, ...],
        *,
        sample_rate: int,
        started_at_ms: float,
    ) -> StreamingSttEvent | None:
        del sample_rate, started_at_ms
        if self._index >= len(self._utterances):
            return None
        events = self._utterances[self._index]
        for index, event in enumerate(events):
            if not event.is_final:
                return events.pop(index)
        return None

    def reset_stream(self) -> None:
        return None


async def observation_events(
    segment: AudioSpeechSegment,
    backend: TranscribesAudio,
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


def create_default_stt_backend(
    *,
    command: str | None = None,
) -> ArgmaxWhisperKitStreamingBackend | AppleSpeechStreamingBackend:
    backend = os.environ.get("TOMOKO_V2_STT_BACKEND", "whisperkit").strip().lower()
    backend = backend.replace("-", "_")
    if backend in {"apple", "apple_speech", "applespeech"}:
        return AppleSpeechStreamingBackend(command=command)
    if backend in {"whisperkit", "argmax", "argmax_whisperkit"}:
        return ArgmaxWhisperKitStreamingBackend(command=command)
    raise RuntimeError(f"unknown TOMOKO_V2_STT_BACKEND: {backend}")


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


def whisperkit_runtime_available() -> dict[str, object]:
    command = _default_whisperkit_command()
    command_path = Path(command)
    resolved = str(command_path) if command_path.exists() else shutil.which(command)
    return {
        "command": resolved is not None,
        "command_path": resolved or command,
        "model": os.environ.get("TOMOKO_V2_WHISPERKIT_MODEL", WHISPERKIT_LARGE_V3_TURBO_MODEL),
        "model_path": os.environ.get("TOMOKO_V2_WHISPERKIT_MODEL_PATH", ""),
        "audio_encoder_compute_units": os.environ.get(
            "TOMOKO_V2_WHISPERKIT_AUDIO_ENCODER_COMPUTE_UNITS",
            WHISPERKIT_DEFAULT_COMPUTE_UNITS,
        ),
        "text_decoder_compute_units": os.environ.get(
            "TOMOKO_V2_WHISPERKIT_TEXT_DECODER_COMPUTE_UNITS",
            WHISPERKIT_DEFAULT_COMPUTE_UNITS,
        ),
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


def _default_whisperkit_command() -> str:
    configured = os.environ.get("TOMOKO_V2_WHISPERKIT_COMMAND")
    if configured:
        return configured
    return shutil.which("argmax-cli") or shutil.which("whisperkit-cli") or "argmax-cli"


def _extract_whisperkit_text(output: str) -> str:
    text = output.strip()
    if not text:
        return ""
    try:
        return _text_from_whisperkit_payload(json.loads(text))
    except json.JSONDecodeError:
        pass

    json_texts: list[str] = []
    plain_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed_text = _text_from_whisperkit_payload(json.loads(line))
        except json.JSONDecodeError:
            parsed_text = ""
        if parsed_text:
            json_texts.append(parsed_text)
            continue
        current = _strip_whisperkit_progress_prefix(line)
        if current:
            plain_lines.append(current)
    if json_texts:
        return "\n".join(json_texts).strip()
    return "\n".join(plain_lines).strip()


def _text_from_whisperkit_payload(payload: object) -> str:
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, list):
        return "\n".join(
            text
            for item in payload
            if (text := _text_from_whisperkit_payload(item))
        ).strip()
    if not isinstance(payload, dict):
        return ""
    for key in ("text", "transcript", "transcription"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("result", "results", "segments"):
        value = payload.get(key)
        parsed = _text_from_whisperkit_payload(value)
        if parsed:
            return parsed
    return ""


def _strip_whisperkit_progress_prefix(line: str) -> str:
    if line.startswith("[") and "]" in line:
        _, line = line.split("]", 1)
        return line.strip()
    for prefix in ("Current Transcription:", "Current transcription:", "Transcription:"):
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    ignored_prefixes = (
        "Loading",
        "Loaded",
        "Downloading",
        "Downloaded",
        "Transcribing",
        "Model",
        "Audio",
        "Progress",
    )
    if line.startswith(ignored_prefixes):
        return ""
    return line


def _datetime_from_ms(ms: float) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


def _console_event(event: str, **fields: object) -> None:
    parts = [f"[tomoko:stt] {event}"]
    for key, value in fields.items():
        text = str(value)
        if len(text) > 120:
            text = text[:117] + "..."
        parts.append(f"{key}={text!r}")
    print(" ".join(parts), flush=True)
