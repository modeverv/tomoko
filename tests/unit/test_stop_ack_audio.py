from __future__ import annotations

import wave
from array import array
from io import BytesIO
from pathlib import Path

import pytest

from server.gateway.stop_ack import StopAckAudioProvider


@pytest.mark.unit
def test_stop_ack_audio_provider_loads_fixed_wav() -> None:
    provider = StopAckAudioProvider(Path("assets/audio/stop_ack.wav"))

    chunk = provider.chunk()

    assert provider.text == "はい、止めます"
    assert chunk.sequence == 0
    assert chunk.is_last is True
    assert chunk.data[:4] == b"RIFF"
    assert chunk.data[8:12] == b"WAVE"

    with wave.open(BytesIO(chunk.data), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 16000
        duration_ms = wav.getnframes() / wav.getframerate() * 1000
        assert duration_ms >= 1300

        tail_frames = int(wav.getframerate() * 0.25)
        wav.setpos(wav.getnframes() - tail_frames)
        tail = wav.readframes(tail_frames)

    samples = array("h")
    samples.frombytes(tail)
    assert max(abs(sample) for sample in samples) == 0
