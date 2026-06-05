from __future__ import annotations

import io
import wave
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from server.shared.config import BackendSpec
from server.shared.inference.tts import create_tts_backend
from server.shared.inference.tts.voicevox import (
    VoicevoxBackend,
    VoicevoxChunkedBackend,
    VoicevoxStreamBackend,
)
from server.shared.models import TTSInput


class FakeResponse:
    def __init__(self, *, json_data: dict[str, Any] | None = None, content: bytes = b"") -> None:
        self._json_data = json_data or {}
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return dict(self._json_data)


class FakeVoicevoxClient:
    def __init__(self, wav: bytes) -> None:
        self.wav = wav
        self.requests: list[dict[str, Any]] = []
        self.closed = False

    async def post(
        self,
        url: str,
        *,
        params: dict[str, Any],
        json: dict[str, Any] | None = None,
    ) -> FakeResponse:
        self.requests.append({"url": url, "params": params, "json": json})
        if url.endswith("/audio_query"):
            return FakeResponse(
                json_data={
                    "speedScale": 1.0,
                    "intonationScale": 1.0,
                    "outputSamplingRate": 44100,
                    "outputStereo": True,
                }
            )
        return FakeResponse(content=self.wav)

    async def aclose(self) -> None:
        self.closed = True


class FakeStreamResponse:
    def __init__(self, chunks: list[bytes], *, status_code: int = 200) -> None:
        self._chunks = chunks
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://127.0.0.1:50021/cancellable_synthesis")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("stream failed", request=request, response=response)
        return None

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class FakeMultipartStreamResponse(FakeStreamResponse):
    def __init__(self, chunks: list[bytes]) -> None:
        super().__init__(chunks)
        self.headers = {
            "content-type": "multipart/mixed; boundary=voicevox-stream-boundary"
        }


class FakeStreamContext:
    def __init__(self, response: FakeStreamResponse) -> None:
        self._response = response

    async def __aenter__(self) -> FakeStreamResponse:
        return self._response

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakeVoicevoxStreamClient(FakeVoicevoxClient):
    def __init__(self, chunks: list[bytes], *, stream_status_code: int = 200) -> None:
        super().__init__(b"")
        self.chunks = chunks
        self.stream_status_code = stream_status_code

    def stream(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any],
        json: dict[str, Any],
    ) -> FakeStreamContext:
        self.requests.append(
            {
                "method": method,
                "url": url,
                "params": params,
                "json": json,
            }
        )
        return FakeStreamContext(
            FakeStreamResponse(self.chunks, status_code=self.stream_status_code)
        )


class FakeVoicevoxChunkedClient(FakeVoicevoxClient):
    def __init__(self, stream_bytes: list[bytes]) -> None:
        super().__init__(b"")
        self.stream_bytes = stream_bytes

    def stream(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any],
        json: dict[str, Any],
    ) -> FakeStreamContext:
        self.requests.append(
            {
                "method": method,
                "url": url,
                "params": params,
                "json": json,
            }
        )
        return FakeStreamContext(FakeMultipartStreamResponse(self.stream_bytes))


class FakeVoicevoxStreamFallbackClient(FakeVoicevoxStreamClient):
    def __init__(self, wav: bytes) -> None:
        super().__init__([], stream_status_code=404)
        self.wav = wav


@pytest.mark.unit
async def test_voicevox_backend_uses_audio_query_then_synthesis() -> None:
    wav_bytes = _wav_bytes()
    client = FakeVoicevoxClient(wav_bytes)
    backend = VoicevoxBackend(
        url="http://127.0.0.1:50021",
        speaker_id=8,
        sample_rate=24000,
        client=client,  # type: ignore[arg-type]
    )

    chunks = [
        chunk
        async for chunk in backend.synthesize(TTSInput(text="こんにちは。", style="happy"))
    ]

    assert [chunk.data for chunk in chunks] == [wav_bytes]
    assert client.requests[0] == {
        "url": "http://127.0.0.1:50021/audio_query",
        "params": {"text": "こんにちは。", "speaker": 8},
        "json": None,
    }
    assert client.requests[1]["url"] == "http://127.0.0.1:50021/synthesis"
    assert client.requests[1]["params"] == {"speaker": 8}
    assert client.requests[1]["json"]["speedScale"] == 1.08
    assert client.requests[1]["json"]["intonationScale"] == 1.08
    assert client.requests[1]["json"]["outputSamplingRate"] == 24000
    assert client.requests[1]["json"]["outputStereo"] is False
    assert client.closed is False


@pytest.mark.unit
async def test_voicevox_stream_backend_uses_cancellable_synthesis() -> None:
    wav_bytes = _wav_bytes()
    client = FakeVoicevoxStreamClient([wav_bytes[:12], wav_bytes[12:]])
    backend = VoicevoxStreamBackend(
        url="http://127.0.0.1:50021",
        speaker_id=8,
        sample_rate=24000,
        client=client,  # type: ignore[arg-type]
    )

    chunks = [
        chunk
        async for chunk in backend.synthesize(TTSInput(text="こんにちは。", style="gentle"))
    ]

    assert [chunk.data for chunk in chunks] == [wav_bytes]
    assert client.requests[0] == {
        "url": "http://127.0.0.1:50021/audio_query",
        "params": {"text": "こんにちは。", "speaker": 8},
        "json": None,
    }
    stream_request = client.requests[1]
    assert stream_request["method"] == "POST"
    assert stream_request["url"] == "http://127.0.0.1:50021/cancellable_synthesis"
    assert stream_request["params"] == {"speaker": 8}
    assert stream_request["json"]["speedScale"] == 0.94
    assert stream_request["json"]["intonationScale"] == 0.92
    assert stream_request["json"]["outputSamplingRate"] == 24000
    assert stream_request["json"]["outputStereo"] is False


@pytest.mark.unit
async def test_voicevox_stream_backend_falls_back_when_experimental_route_is_disabled() -> None:
    wav_bytes = _wav_bytes()
    client = FakeVoicevoxStreamFallbackClient(wav_bytes)
    backend = VoicevoxStreamBackend(
        url="http://127.0.0.1:50021",
        speaker_id=8,
        sample_rate=24000,
        client=client,  # type: ignore[arg-type]
    )

    chunks = [
        chunk
        async for chunk in backend.synthesize(TTSInput(text="うん。", style="neutral"))
    ]

    assert [chunk.data for chunk in chunks] == [wav_bytes]
    assert client.requests[1]["url"] == "http://127.0.0.1:50021/cancellable_synthesis"
    assert client.requests[2]["url"] == "http://127.0.0.1:50021/synthesis"
    assert client.requests[2]["params"] == {"speaker": 8}


@pytest.mark.unit
async def test_voicevox_chunked_backend_yields_multipart_wav_chunks() -> None:
    first_wav = _wav_bytes()
    second_wav = _wav_bytes()
    stream_bytes = _multipart_stream_bytes(
        [
            (0, False, first_wav),
            (1, True, second_wav),
        ]
    )
    client = FakeVoicevoxChunkedClient(stream_bytes)
    backend = VoicevoxChunkedBackend(
        url="http://127.0.0.1:50021",
        speaker_id=8,
        sample_rate=16000,
        chunk_min_accent_phrases=1,
        client=client,  # type: ignore[arg-type]
    )

    chunks = [
        chunk
        async for chunk in backend.synthesize(TTSInput(text="こんにちは。", style="happy"))
    ]

    assert [chunk.data for chunk in chunks] == [first_wav, second_wav]
    assert [chunk.sequence for chunk in chunks] == [0, 1]
    assert [chunk.is_last for chunk in chunks] == [False, True]
    assert client.requests[0] == {
        "url": "http://127.0.0.1:50021/audio_query",
        "params": {"text": "こんにちは。", "speaker": 8},
        "json": None,
    }
    stream_request = client.requests[1]
    assert stream_request["method"] == "POST"
    assert stream_request["url"] == "http://127.0.0.1:50021/streaming_synthesis"
    assert stream_request["params"] == {
        "speaker": 8,
        "chunk_min_accent_phrases": 1,
    }
    assert stream_request["json"]["speedScale"] == 1.08
    assert stream_request["json"]["intonationScale"] == 1.08
    assert stream_request["json"]["outputSamplingRate"] == 16000
    assert stream_request["json"]["outputStereo"] is False


@pytest.mark.unit
async def test_voicevox_backend_accepts_kasukabe_tsumugi_alias() -> None:
    client = FakeVoicevoxClient(_wav_bytes())
    backend = VoicevoxBackend(client=client)  # type: ignore[arg-type]

    chunks = [
        chunk
        async for chunk in backend.synthesize(
            TTSInput(text="うん。", style="neutral", voice="春日部つむぎ")
        )
    ]

    assert len(chunks) == 1
    assert client.requests[0]["params"] == {"text": "うん。", "speaker": 8}


@pytest.mark.unit
def test_tts_factory_creates_voicevox_backend() -> None:
    backend = create_tts_backend(
        BackendSpec(
            name="voicevox_tsumugi",
            type="voicevox",
            url="http://127.0.0.1:50021",
            voice="8",
            sample_rate=24000,
        )
    )

    assert isinstance(backend, VoicevoxBackend)
    assert backend.url == "http://127.0.0.1:50021"
    assert backend.speaker_id == 8
    assert backend.sample_rate == 24000


@pytest.mark.unit
def test_tts_factory_creates_voicevox_stream_backend() -> None:
    backend = create_tts_backend(
        BackendSpec(
            name="voicevox_tsumugi_stream",
            type="voicevox_stream",
            url="http://127.0.0.1:50021",
            voice="8",
            sample_rate=24000,
        )
    )

    assert isinstance(backend, VoicevoxStreamBackend)
    assert backend.url == "http://127.0.0.1:50021"
    assert backend.speaker_id == 8
    assert backend.sample_rate == 24000


@pytest.mark.unit
def test_tts_factory_creates_voicevox_chunked_backend() -> None:
    backend = create_tts_backend(
        BackendSpec(
            name="voicevox_tsumugi_chunked",
            type="voicevox_chunked",
            url="http://127.0.0.1:50021",
            voice="8",
            sample_rate=16000,
            chunk_min_accent_phrases=1,
        )
    )

    assert isinstance(backend, VoicevoxChunkedBackend)
    assert backend.url == "http://127.0.0.1:50021"
    assert backend.speaker_id == 8
    assert backend.sample_rate == 16000
    assert backend.chunk_min_accent_phrases == 1


def _wav_bytes() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x00\x01\x00")
    return output.getvalue()


def _multipart_stream_bytes(parts: list[tuple[int, bool, bytes]]) -> list[bytes]:
    boundary = "voicevox-stream-boundary"
    payload = bytearray()
    for sequence, is_last, body in parts:
        payload.extend(
            (
                f"--{boundary}\r\n"
                "Content-Type: audio/wav\r\n"
                f"X-Sequence: {sequence}\r\n"
                f"X-Is-Last: {str(is_last).lower()}\r\n"
                f"Content-Length: {len(body)}\r\n"
                "\r\n"
            ).encode("ascii")
        )
        payload.extend(body)
        payload.extend(b"\r\n")
    payload.extend(f"--{boundary}--\r\n".encode("ascii"))
    return [bytes(payload[:17]), bytes(payload[17:89]), bytes(payload[89:])]
