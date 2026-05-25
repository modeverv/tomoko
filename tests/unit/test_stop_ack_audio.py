from __future__ import annotations

import wave
from io import BytesIO
from pathlib import Path

import pytest

from server.gateway.stop_ack import StopAckAudioProvider


@pytest.mark.unit
def test_stop_ack_audio_provider_loads_fixed_wav() -> None:
    provider = StopAckAudioProvider(Path("assets/audio/stop_ack.wav"))

    chunk = provider.chunk()

    assert provider.text == "はい、止めますね"
    assert chunk.sequence == 0
    assert chunk.is_last is True
    assert chunk.data[:4] == b"RIFF"
    assert chunk.data[8:12] == b"WAVE"

    with wave.open(BytesIO(chunk.data), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 44100
        duration_ms = wav.getnframes() / wav.getframerate() * 1000
        assert duration_ms >= 1700
