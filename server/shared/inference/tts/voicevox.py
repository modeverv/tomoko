from __future__ import annotations

import struct
import time
from collections.abc import AsyncGenerator, Mapping
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


class VoicevoxChunkedBackend(VoicevoxBackend):
    name = "voicevox_chunked"

    def __init__(
        self,
        *,
        url: str = DEFAULT_VOICEVOX_URL,
        speaker_id: int = KASUKABE_TSUMUGI_SPEAKER_ID,
        sample_rate: int | None = None,
        speed: float | None = None,
        chunk_min_accent_phrases: int = 1,
        segment_length: float = 0.6,
        client: httpx.AsyncClient | None = None,
        timeout_sec: float = 30.0,
    ) -> None:
        super().__init__(
            url=url,
            speaker_id=speaker_id,
            sample_rate=sample_rate,
            speed=speed,
            client=client,
            timeout_sec=timeout_sec,
        )
        self.chunk_min_accent_phrases = chunk_min_accent_phrases
        self.segment_length = segment_length

    @classmethod
    def from_spec(cls, spec: BackendSpec) -> VoicevoxChunkedBackend:
        return cls(
            url=spec.url or DEFAULT_VOICEVOX_URL,
            speaker_id=_speaker_id_from_voice(spec.voice),
            sample_rate=spec.sample_rate,
            speed=spec.speed,
            chunk_min_accent_phrases=spec.chunk_min_accent_phrases or 1,
            segment_length=spec.segment_length or 0.6,
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
        chunk_count = 0
        first_chunk_emitted = False
        try:
            audio_query = await self._audio_query(client, text, speaker_id, tts_input.style)

            async with client.stream(
                "POST",
                f"{self.url}/streaming_synthesis",
                params={
                    "speaker": speaker_id,
                    "chunk_min_accent_phrases": self.chunk_min_accent_phrases,
                    "segment_length": self.segment_length,
                },
                json=audio_query,
            ) as response:
                response.raise_for_status()
                async for chunk in _iter_streaming_synthesis_chunks(
                    response,
                    wav_chunk_audio_sec=self.segment_length,
                ):
                    if not first_chunk_emitted:
                        first_chunk_emitted = True
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
                            sequence=chunk.sequence,
                        )
                    chunk_count += 1
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
                chunk_count=chunk_count,
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


async def _iter_streaming_synthesis_chunks(
    response: Any,
    *,
    wav_chunk_audio_sec: float,
) -> AsyncGenerator[AudioChunkOut, None]:
    headers = getattr(response, "headers", {})
    content_type = headers.get("content-type", "")
    if _parse_multipart_boundary(content_type) is not None:
        async for chunk in _iter_multipart_audio_chunks(response):
            yield chunk
        return
    if content_type.lower().partition(";")[0].strip() == "audio/wav":
        async for chunk in _iter_wav_stream_audio_chunks(
            response,
            chunk_audio_sec=wav_chunk_audio_sec,
        ):
            yield chunk
        return
    raise ValueError(f"unsupported streaming_synthesis content-type: {content_type!r}")


async def _iter_multipart_audio_chunks(
    response: Any,
) -> AsyncGenerator[AudioChunkOut, None]:
    headers = getattr(response, "headers", {})
    content_type = headers.get("content-type", "")
    boundary = _parse_multipart_boundary(content_type)
    if boundary is None:
        raise ValueError(f"missing multipart boundary in content-type: {content_type!r}")

    parser = _MultipartMixedParser(boundary)
    async for data in response.aiter_bytes():
        for part_headers, body in parser.feed(data):
            yield _audio_chunk_from_part(part_headers, body)
    for part_headers, body in parser.finish():
        yield _audio_chunk_from_part(part_headers, body)


async def _iter_wav_stream_audio_chunks(
    response: Any,
    *,
    chunk_audio_sec: float,
) -> AsyncGenerator[AudioChunkOut, None]:
    chunker = _WavStreamChunker(chunk_audio_sec=chunk_audio_sec)
    async for data in response.aiter_bytes():
        for chunk in chunker.feed(data):
            yield chunk
    final_chunks = chunker.finish()
    for index, chunk in enumerate(final_chunks):
        yield AudioChunkOut(
            data=chunk.data,
            sequence=chunk.sequence,
            is_last=index == len(final_chunks) - 1,
        )


def _parse_multipart_boundary(content_type: str) -> str | None:
    for part in content_type.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key.lower() == "boundary":
            return value.strip('"')
    return None


def _audio_chunk_from_part(headers: Mapping[str, str], body: bytes) -> AudioChunkOut:
    is_last_header = headers.get("x-is-last")
    return AudioChunkOut(
        data=body,
        sequence=int(headers["x-sequence"]),
        is_last=is_last_header.lower() == "true" if is_last_header is not None else False,
    )


class _MultipartMixedParser:
    def __init__(self, boundary: str) -> None:
        self._boundary_line = f"--{boundary}\r\n".encode("ascii")
        self._closing_boundary_line = f"--{boundary}--\r\n".encode("ascii")
        self._buffer = bytearray()
        self._closed = False

    def feed(self, data: bytes) -> list[tuple[dict[str, str], bytes]]:
        self._buffer.extend(data)
        return list(self._parse_available_parts())

    def finish(self) -> list[tuple[dict[str, str], bytes]]:
        parts = list(self._parse_available_parts())
        if self._buffer and not self._closed:
            raise ValueError("incomplete multipart response")
        return parts

    def _parse_available_parts(self) -> list[tuple[dict[str, str], bytes]]:
        parts: list[tuple[dict[str, str], bytes]] = []
        while True:
            if self._closed:
                return parts

            if self._buffer.startswith(self._closing_boundary_line):
                del self._buffer[: len(self._closing_boundary_line)]
                self._closed = True
                return parts

            if not self._buffer.startswith(self._boundary_line):
                return parts

            header_start = len(self._boundary_line)
            header_end = self._buffer.find(b"\r\n\r\n", header_start)
            if header_end < 0:
                return parts

            headers = _parse_part_headers(bytes(self._buffer[header_start:header_end]))
            content_length = int(headers["content-length"])
            body_start = header_end + len(b"\r\n\r\n")
            body_end = body_start + content_length
            part_end = body_end + len(b"\r\n")
            if len(self._buffer) < part_end:
                return parts

            body = bytes(self._buffer[body_start:body_end])
            if self._buffer[body_end:part_end] != b"\r\n":
                raise ValueError("multipart part body is not followed by CRLF")

            del self._buffer[:part_end]
            parts.append((headers, body))


def _parse_part_headers(header_bytes: bytes) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in header_bytes.decode("ascii").split("\r\n"):
        key, separator, value = line.partition(":")
        if not separator:
            raise ValueError(f"invalid multipart header line: {line!r}")
        headers[key.lower()] = value.strip()
    return headers


class _WavStreamChunker:
    def __init__(self, *, chunk_audio_sec: float) -> None:
        self._chunk_audio_sec = max(0.05, chunk_audio_sec)
        self._buffer = bytearray()
        self._format: _WavFormat | None = None
        self._sequence = 0

    def feed(self, data: bytes) -> list[AudioChunkOut]:
        self._buffer.extend(data)
        self._ensure_header_parsed()
        return list(self._pop_ready_chunks(final=False))

    def finish(self) -> list[AudioChunkOut]:
        self._ensure_header_parsed()
        return list(self._pop_ready_chunks(final=True))

    def _ensure_header_parsed(self) -> None:
        if self._format is not None:
            return
        parsed = _try_parse_wav_header(self._buffer)
        if parsed is None:
            return
        wav_format, data_start = parsed
        self._format = wav_format
        del self._buffer[:data_start]

    def _pop_ready_chunks(self, *, final: bool) -> list[AudioChunkOut]:
        if self._format is None:
            if final and self._buffer:
                raise ValueError("incomplete WAV stream header")
            return []

        chunk_bytes = max(
            self._format.block_align,
            int(self._format.byte_rate * self._chunk_audio_sec),
        )
        chunk_bytes -= chunk_bytes % self._format.block_align
        chunks: list[AudioChunkOut] = []
        while len(self._buffer) >= chunk_bytes > 0:
            chunks.append(self._make_chunk(bytes(self._buffer[:chunk_bytes])))
            del self._buffer[:chunk_bytes]
        if final and self._buffer:
            aligned = len(self._buffer) - (len(self._buffer) % self._format.block_align)
            if aligned:
                chunks.append(self._make_chunk(bytes(self._buffer[:aligned])))
                del self._buffer[:aligned]
            if self._buffer:
                raise ValueError("WAV stream ended with a partial audio frame")
        return chunks

    def _make_chunk(self, pcm: bytes) -> AudioChunkOut:
        assert self._format is not None
        chunk = AudioChunkOut(
            data=_wrap_pcm_as_wav(pcm, self._format),
            sequence=self._sequence,
            is_last=False,
        )
        self._sequence += 1
        return chunk


class _WavFormat:
    def __init__(
        self,
        *,
        channels: int,
        sample_rate: int,
        byte_rate: int,
        block_align: int,
        bits_per_sample: int,
    ) -> None:
        self.channels = channels
        self.sample_rate = sample_rate
        self.byte_rate = byte_rate
        self.block_align = block_align
        self.bits_per_sample = bits_per_sample


def _try_parse_wav_header(buffer: bytearray) -> tuple[_WavFormat, int] | None:
    if len(buffer) < 12:
        return None
    if buffer[:4] != b"RIFF" or buffer[8:12] != b"WAVE":
        raise ValueError("streaming_synthesis audio/wav response is not RIFF/WAVE")

    offset = 12
    wav_format: _WavFormat | None = None
    while len(buffer) >= offset + 8:
        chunk_id = bytes(buffer[offset : offset + 4])
        chunk_size = int.from_bytes(buffer[offset + 4 : offset + 8], "little")
        chunk_start = offset + 8
        chunk_end = chunk_start + chunk_size
        if chunk_id == b"data":
            if wav_format is None:
                raise ValueError("WAV data chunk appeared before fmt chunk")
            return wav_format, chunk_start
        if len(buffer) < chunk_end:
            return None
        if chunk_id == b"fmt ":
            wav_format = _parse_wav_fmt(bytes(buffer[chunk_start:chunk_end]))
        offset = chunk_end + (chunk_size % 2)
    return None


def _parse_wav_fmt(fmt: bytes) -> _WavFormat:
    if len(fmt) < 16:
        raise ValueError("WAV fmt chunk is too short")
    (
        _audio_format,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
    ) = struct.unpack_from("<HHIIHH", fmt)
    if channels <= 0 or sample_rate <= 0 or byte_rate <= 0 or block_align <= 0:
        raise ValueError("WAV fmt chunk contains invalid audio parameters")
    return _WavFormat(
        channels=channels,
        sample_rate=sample_rate,
        byte_rate=byte_rate,
        block_align=block_align,
        bits_per_sample=bits_per_sample,
    )


def _wrap_pcm_as_wav(pcm: bytes, wav_format: _WavFormat) -> bytes:
    fmt_chunk = struct.pack(
        "<HHIIHH",
        1,
        wav_format.channels,
        wav_format.sample_rate,
        wav_format.byte_rate,
        wav_format.block_align,
        wav_format.bits_per_sample,
    )
    data_size = len(pcm)
    riff_size = 4 + (8 + len(fmt_chunk)) + (8 + data_size)
    return b"".join(
        [
            b"RIFF",
            struct.pack("<I", riff_size),
            b"WAVE",
            b"fmt ",
            struct.pack("<I", len(fmt_chunk)),
            fmt_chunk,
            b"data",
            struct.pack("<I", data_size),
            pcm,
        ]
    )
