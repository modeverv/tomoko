from __future__ import annotations

import asyncio
import atexit
import shutil
import subprocess
from datetime import UTC, datetime
from time import perf_counter
from types import TracebackType
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import numpy as np

from server.edge.pipeline.stt_coreml import _audio_level_db, _write_temp_wav
from server.shared.inference.trace import trace_backend_call
from server.shared.models import SpeechSegment, Transcript


class WhisperKitServeSTT:
    def __init__(
        self,
        *,
        url: str = "http://127.0.0.1:50060",
        model_name: str = "small",
        command: str = "whisperkit-cli",
        language: str = "ja",
        initial_prompt: str = "ともこ",
        streaming: bool = False,
        stream_interval_ms: int = 1000,
        stream_min_audio_ms: int = 1000,
        startup_timeout_s: float = 60.0,
        client: Any | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.model_name = model_name
        self.command = command
        self.language = language
        self.initial_prompt = initial_prompt
        self.streaming = streaming
        self.stream_interval_ms = stream_interval_ms
        self.stream_min_audio_ms = stream_min_audio_ms
        self.startup_timeout_s = startup_timeout_s
        self._client = client
        self._process: subprocess.Popen[bytes] | None = None
        self._server_ready = False
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
            backend="whisperkit_serve",
            model=self.model_name,
            request_id=request_id,
            queue_key=f"whisperkit:{self.url}",
            audio_ms=_audio_ms(segment.audio, 16000),
        )
        try:
            text = await self._transcribe_audio(segment.audio, 16000)
        except Exception as exc:
            trace_backend_call(
                event="error",
                kind="stt",
                role="stt",
                backend="whisperkit_serve",
                model=self.model_name,
                request_id=request_id,
                queue_key=f"whisperkit:{self.url}",
                total_ms=_elapsed_ms(started_at),
                error=type(exc).__name__,
            )
            raise
        trace_backend_call(
            event="done",
            kind="stt",
            role="stt",
            backend="whisperkit_serve",
            model=self.model_name,
            request_id=request_id,
            queue_key=f"whisperkit:{self.url}",
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
        await self._ensure_server()
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
        text = await self._transcribe_audio(audio, sample_rate)
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

    async def close(self) -> None:
        await self._close_client()
        self._terminate_process()

    async def __aenter__(self) -> WhisperKitServeSTT:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        await self.close()

    async def _transcribe_audio(self, audio: np.ndarray, sample_rate: int) -> str:
        await self._ensure_server()
        audio_path = _write_temp_wav(audio, sample_rate)
        try:
            with audio_path.open("rb") as audio_file:
                response = await self._client_instance().post(
                    f"{self.url}/v1/audio/transcriptions",
                    files={"file": (audio_path.name, audio_file, "audio/wav")},
                    data={
                        "model": self.model_name,
                        "language": self.language,
                        "prompt": self.initial_prompt,
                    },
                )
            response.raise_for_status()
            payload = response.json()
        finally:
            audio_path.unlink(missing_ok=True)
        return str(payload.get("text", "")).strip()

    async def _ensure_server(self) -> None:
        if self._server_ready and await self._is_healthy():
            return
        if await self._is_healthy():
            self._server_ready = True
            return
        self._start_process()
        await self._wait_until_healthy()
        self._server_ready = True

    async def _is_healthy(self) -> bool:
        try:
            response = await self._client_instance().get(f"{self.url}/health")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def _wait_until_healthy(self) -> None:
        deadline = asyncio.get_running_loop().time() + self.startup_timeout_s
        last_error: Exception | None = None
        while asyncio.get_running_loop().time() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise RuntimeError(f"WhisperKit serve exited with code {self._process.returncode}")
            try:
                if await self._is_healthy():
                    return
            except Exception as e:  # noqa: BLE001
                last_error = e
            await asyncio.sleep(0.25)
        if last_error is not None:
            raise RuntimeError("WhisperKit serve did not become healthy") from last_error
        raise RuntimeError("WhisperKit serve did not become healthy")

    def _start_process(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        if shutil.which(self.command) is None:
            raise RuntimeError(
                f"{self.command!r} is not available. Install whisperkit-cli or set "
                "backends.<name>.command."
            )
        host, port = _host_port_from_url(self.url)
        self._process = subprocess.Popen(
            [
                self.command,
                "serve",
                "--model",
                self.model_name,
                "--language",
                self.language,
                "--prompt",
                self.initial_prompt,
                "--without-timestamps",
                "--host",
                host,
                "--port",
                str(port),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        atexit.register(self._terminate_process)

    def _terminate_process(self) -> None:
        if self._process is None or self._process.poll() is not None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=5)

    def _client_instance(self) -> Any:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=2.0))
        return self._client

    async def _close_client(self) -> None:
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()


def _host_port_from_url(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, port


def _audio_ms(audio: np.ndarray, sample_rate: int) -> float:
    if sample_rate <= 0:
        return 0.0
    return len(audio) / sample_rate * 1000.0


def _elapsed_ms(started_at: float) -> float:
    return (perf_counter() - started_at) * 1000
