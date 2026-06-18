from __future__ import annotations

import io
import wave
from types import SimpleNamespace

import numpy as np
import pytest

from server.shared.config import BackendSpec
from server.shared.inference.tts import create_tts_backend
from server.shared.inference.tts.irodori_mlx import IrodoriMLXBackend
from server.shared.models import TTSInput


class FakeIrodoriModel:
    sample_rate = 48000

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        yield SimpleNamespace(
            audio=np.array([0.0, 0.5, -0.5], dtype=np.float32),
            sample_rate=48000,
        )


@pytest.mark.unit
async def test_irodori_mlx_backend_loads_mlx_audio_model_and_returns_wav_chunk() -> None:
    fake_model = FakeIrodoriModel()
    loaded: list[str] = []

    backend = IrodoriMLXBackend(
        model="mlx-community/Irodori-TTS-500M-v3-8bit",
        voice="none",
        model_factory=lambda model_name: loaded.append(model_name) or fake_model,
    )

    chunks = [
        chunk
        async for chunk in backend.synthesize(TTSInput(text="こんにちは。", style="happy"))
    ]

    assert loaded == ["mlx-community/Irodori-TTS-500M-v3-8bit"]
    assert len(chunks) == 1
    assert chunks[0].sequence == 0
    assert chunks[0].is_last is True
    assert chunks[0].data.startswith(b"RIFF")
    with wave.open(io.BytesIO(chunks[0].data), "rb") as wav:
        assert wav.getframerate() == 48000
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getnframes() == 3
    assert fake_model.calls == [
        {
            "text": "こんにちは。",
            "ref_audio": None,
            "duration_scale": 0.95,
            "num_steps": 24,
            "t_schedule_mode": "sway",
            "sway_coeff": -1.0,
        }
    ]


@pytest.mark.unit
async def test_irodori_mlx_backend_uses_voice_as_reference_audio_path() -> None:
    fake_model = FakeIrodoriModel()
    backend = IrodoriMLXBackend(loaded_model=fake_model, voice="voices/tomoko.wav")

    _ = [chunk async for chunk in backend.synthesize(TTSInput(text="大丈夫。", style="sad"))]

    assert fake_model.calls[0]["ref_audio"] == "voices/tomoko.wav"
    assert fake_model.calls[0]["duration_scale"] == 1.08


@pytest.mark.unit
def test_tts_factory_creates_irodori_mlx_backend() -> None:
    backend = create_tts_backend(
        BackendSpec(
            name="irodori_mlx",
            type="irodori_mlx",
            model="mlx-community/Irodori-TTS-500M-v3-8bit",
            voice="none",
        )
    )

    assert isinstance(backend, IrodoriMLXBackend)
