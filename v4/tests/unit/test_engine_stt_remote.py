import io
import wave

import httpx
import numpy as np
import pytest

from engine.sensors.stt_remote import WhisperCppStt

pytestmark = pytest.mark.unit


async def test_remote_stt_posts_a_16k_wav_and_returns_the_text():
    seen = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen["path"], seen["body"] = request.url.path, request.content
        return httpx.Response(200, json={"text": " こんにちは。\n"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        text = await WhisperCppStt(client, "http://stt/").transcribe(np.full(1600, 0.25, dtype=np.float32))
    assert text == "こんにちは。"
    assert seen["path"] == "/inference"
    start = seen["body"].index(b"RIFF")
    with wave.open(io.BytesIO(seen["body"][start:])) as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (16_000, 1, 1600)
    assert b'name="language"' in seen["body"]
