from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

import httpx

from server.shared.config import BackendSpec
from server.shared.inference.trace import trace_backend_call
from server.shared.inference.tts.base import TTSBackend
from server.shared.models import AudioChunkOut, TTSInput

DEFAULT_VOICEVOX_URL = "http://127.0.0.1:50021"
KASUKABE_TSUMUGI_SPEAKER_ID = 8


class VoicevoxBackend(TTSBackend):
    name = "voicevox"

    STYLE_TO_SPEED = {
        "neutral": 1.0,
        "happy": 1.08,
        "surprised": 1.06,
        "excited": 1.12,
        "sad": 0.92,
        "thinking": 0.96,
        "gentle": 0.94,
    }
    STYLE_TO_INTONATION = {
        "neutral": 1.0,
        "happy": 1.08,
        "surprised": 1.1,
        "excited": 1.12,
        "sad": 0.88,
        "thinking": 0.95,
        "gentle": 0.92,
    }
    SPEAKER_ALIASES = {
        "kasukabe_tsumugi": KASUKABE_TSUMUGI_SPEAKER_ID,
        "春日部つむぎ": KASUKABE_TSUMUGI_SPEAKER_ID,
        "春日つむぎ": KASUKABE_TSUMUGI_SPEAKER_ID,
        "tsumugi": KASUKABE_TSUMUGI_SPEAKER_ID,
    }

    def __init__(
        self,
        *,
        url: str = DEFAULT_VOICEVOX_URL,
        speaker_id: int = KASUKABE_TSUMUGI_SPEAKER_ID,
        sample_rate: int | None = None,
        speed: float | None = None,
        client: httpx.AsyncClient | None = None,
        timeout_sec: float = 30.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.speaker_id = speaker_id
        self.sample_rate = sample_rate
        self.speed = speed
        self._client = client
        self._timeout_sec = timeout_sec

    @classmethod
    def from_spec(cls, spec: BackendSpec) -> VoicevoxBackend:
        return cls(
            url=spec.url or DEFAULT_VOICEVOX_URL,
            speaker_id=_speaker_id_from_voice(spec.voice),
            sample_rate=spec.sample_rate,
            speed=spec.speed,
        )

    async def warm_up(self) -> None:
        async for _ in self.synthesize(TTSInput(text="あ。", style="neutral")):
            return

    async def synthesize(self, tts_input: TTSInput):
        text = tts_input.text.strip()
        if not text:
            return

        request_id = str(uuid4())
        started_at = time.perf_counter()
        trace_backend_call(
            event="start",
            kind="tts",
            role="tts",
            backend=self.name,
            model="voicevox_engine",
            request_id=request_id,
            queue_key=f"voicevox:{self.url}",
        )
        speaker_id = _speaker_id_from_voice(tts_input.voice) if tts_input.voice else self.speaker_id
        client = self._client or self._create_client()
        close_client = self._client is None
        try:
            audio_query = await self._audio_query(client, text, speaker_id, tts_input.style)

            synthesis_response = await client.post(
                f"{self.url}/synthesis",
                params={"speaker": speaker_id},
                json=audio_query,
            )
            synthesis_response.raise_for_status()
            chunk = AudioChunkOut(
                data=synthesis_response.content,
                sequence=0,
                is_last=True,
            )
            trace_backend_call(
                event="first_chunk",
                kind="tts",
                role="tts",
                backend=self.name,
                model="voicevox_engine",
                request_id=request_id,
                queue_key=f"voicevox:{self.url}",
                elapsed_ms=_elapsed_ms(started_at),
                bytes=len(chunk.data),
            )
            yield chunk
        except Exception as exc:
            trace_backend_call(
                event="error",
                kind="tts",
                role="tts",
                backend=self.name,
                model="voicevox_engine",
                request_id=request_id,
                queue_key=f"voicevox:{self.url}",
                total_ms=_elapsed_ms(started_at),
                error=type(exc).__name__,
            )
            raise
        else:
            trace_backend_call(
                event="done",
                kind="tts",
                role="tts",
                backend=self.name,
                model="voicevox_engine",
                request_id=request_id,
                queue_key=f"voicevox:{self.url}",
                total_ms=_elapsed_ms(started_at),
                chunk_count=1,
            )
        finally:
            if close_client:
                await client.aclose()

    def _create_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout_sec, connect=2.0),
        )

    async def _audio_query(
        self,
        client: httpx.AsyncClient,
        text: str,
        speaker_id: int,
        style: str,
    ) -> dict[str, Any]:
        query_response = await client.post(
            f"{self.url}/audio_query",
            params={"text": text, "speaker": speaker_id},
        )
        query_response.raise_for_status()
        audio_query = query_response.json()
        _apply_style(
            audio_query,
            style=style,
            sample_rate=self.sample_rate,
            speed=self.speed,
        )
        return audio_query


class VoicevoxStreamBackend(VoicevoxBackend):
    name = "voicevox_stream"

    @classmethod
    def from_spec(cls, spec: BackendSpec) -> VoicevoxStreamBackend:
        return cls(
            url=spec.url or DEFAULT_VOICEVOX_URL,
            speaker_id=_speaker_id_from_voice(spec.voice),
            sample_rate=spec.sample_rate,
            speed=spec.speed,
        )

    async def synthesize(
        self,
        tts_input: TTSInput,
    ) -> AsyncGenerator[AudioChunkOut, None]:
        text = tts_input.text.strip()
        if not text:
            return

        request_id = str(uuid4())
        started_at = time.perf_counter()
        trace_backend_call(
            event="start",
            kind="tts",
            role="tts",
            backend=self.name,
            model="voicevox_engine",
            request_id=request_id,
            queue_key=f"voicevox:{self.url}",
        )
        speaker_id = _speaker_id_from_voice(tts_input.voice) if tts_input.voice else self.speaker_id
        client = self._client or self._create_client()
        close_client = self._client is None
        try:
            audio_query = await self._audio_query(client, text, speaker_id, tts_input.style)

            wav_parts: list[bytes] = []
            try:
                async with client.stream(
                    "POST",
                    f"{self.url}/cancellable_synthesis",
                    params={"speaker": speaker_id},
                    json=audio_query,
                ) as response:
                    response.raise_for_status()
                    async for part in response.aiter_bytes():
                        if part:
                            wav_parts.append(part)
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 404:
                    raise
                synthesis_response = await client.post(
                    f"{self.url}/synthesis",
                    params={"speaker": speaker_id},
                    json=audio_query,
                )
                synthesis_response.raise_for_status()
                wav_parts = [synthesis_response.content]

            if wav_parts:
                # The browser currently decodes each websocket binary message as a
                # complete audio file, so preserve one valid WAV per chunk.
                chunk = AudioChunkOut(
                    data=b"".join(wav_parts),
                    sequence=0,
                    is_last=True,
                )
                trace_backend_call(
                    event="first_chunk",
                    kind="tts",
                    role="tts",
                    backend=self.name,
                    model="voicevox_engine",
                    request_id=request_id,
                    queue_key=f"voicevox:{self.url}",
                    elapsed_ms=_elapsed_ms(started_at),
                    bytes=len(chunk.data),
                )
                yield chunk
        except Exception as exc:
            trace_backend_call(
                event="error",
                kind="tts",
                role="tts",
                backend=self.name,
                model="voicevox_engine",
                request_id=request_id,
                queue_key=f"voicevox:{self.url}",
                total_ms=_elapsed_ms(started_at),
                error=type(exc).__name__,
            )
            raise
        else:
            trace_backend_call(
                event="done",
                kind="tts",
                role="tts",
                backend=self.name,
                model="voicevox_engine",
                request_id=request_id,
                queue_key=f"voicevox:{self.url}",
                total_ms=_elapsed_ms(started_at),
                chunk_count=1 if wav_parts else 0,
            )
        finally:
            if close_client:
                await client.aclose()


def _speaker_id_from_voice(voice: str | None) -> int:
    if voice is None or not voice.strip():
        return KASUKABE_TSUMUGI_SPEAKER_ID
    normalized = voice.strip()
    if normalized in VoicevoxBackend.SPEAKER_ALIASES:
        return VoicevoxBackend.SPEAKER_ALIASES[normalized]
    try:
        return int(normalized)
    except ValueError as e:
        raise ValueError(f"unsupported VOICEVOX speaker voice: {voice}") from e


def _apply_style(
    audio_query: dict[str, Any],
    *,
    style: str,
    sample_rate: int | None,
    speed: float | None,
) -> None:
    audio_query["speedScale"] = speed or VoicevoxBackend.STYLE_TO_SPEED.get(style, 1.0)
    audio_query["intonationScale"] = VoicevoxBackend.STYLE_TO_INTONATION.get(style, 1.0)
    if sample_rate is not None:
        audio_query["outputSamplingRate"] = sample_rate
    audio_query["outputStereo"] = False


def _elapsed_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000
