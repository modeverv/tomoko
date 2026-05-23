from __future__ import annotations

import io
import wave
from types import SimpleNamespace

import numpy as np
import pytest

from server.shared.config import BackendSpec
from server.shared.inference.tts import create_tts_backend
from server.shared.inference.tts.qwen3_mlx import Qwen3MLXTTSBackend
from server.shared.models import TTSInput


class FakeQwen3Model:
    sample_rate = 24000

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        yield SimpleNamespace(
            audio=np.array([0.0, 0.2, -0.2], dtype=np.float32),
            sample_rate=24000,
        )
        yield SimpleNamespace(
            audio=np.array([0.1, -0.1], dtype=np.float32),
            sample_rate=24000,
        )


@pytest.mark.unit
async def test_qwen3_mlx_backend_streams_wav_chunks() -> None:
    fake_model = FakeQwen3Model()
    loaded: list[str] = []
    backend = Qwen3MLXTTSBackend(
        model="mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit",
        model_factory=lambda model_name: loaded.append(model_name) or fake_model,
    )

    chunks = [
        chunk
        async for chunk in backend.synthesize(TTSInput(text="こんにちは。", style="happy"))
    ]

    assert loaded == ["mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"]
    assert [chunk.sequence for chunk in chunks] == [0, 1]
    assert [chunk.is_last for chunk in chunks] == [False, True]
    with wave.open(io.BytesIO(chunks[0].data), "rb") as wav:
        assert wav.getframerate() == 24000
        assert wav.getnchannels() == 1
        assert wav.getnframes() == 3
    assert fake_model.calls == [
        {
            "text": "こんにちは。",
            "voice": None,
            "instruct": "明るく、自然な日本語で話す。",
            "lang_code": "Japanese",
            "speed": 0.95,
            "stream": True,
            "streaming_interval": 0.32,
            "split_pattern": "\n",
        }
    ]


@pytest.mark.unit
async def test_qwen3_mlx_backend_uses_configured_voice() -> None:
    fake_model = FakeQwen3Model()
    backend = Qwen3MLXTTSBackend(loaded_model=fake_model, voice="Chelsie")

    _ = [chunk async for chunk in backend.synthesize(TTSInput(text="大丈夫。", style="sad"))]

    assert fake_model.calls[0]["voice"] == "Chelsie"
    assert fake_model.calls[0]["speed"] == 0.92
    assert fake_model.calls[0]["instruct"] == "落ち着いて、やさしい日本語で話す。"


@pytest.mark.unit
def test_tts_factory_creates_qwen3_mlx_backend() -> None:
    backend = create_tts_backend(
        BackendSpec(
            name="qwen3_tts_mlx_small",
            type="qwen3_mlx",
            model="mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit",
            voice="none",
        )
    )

    assert isinstance(backend, Qwen3MLXTTSBackend)
