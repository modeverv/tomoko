from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from server.shared.config import BackendSpec
from server.shared.inference.tts import create_tts_backend
from server.shared.inference.tts.irodori_mlx_stream import (
    IrodoriMLXStreamBackend,
    split_streaming_units,
)
from server.shared.models import TTSInput


class FakeIrodoriStreamModel:
    sample_rate = 48000

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        yield SimpleNamespace(
            audio=np.array([0.0, 0.25, -0.25], dtype=np.float32),
            sample_rate=48000,
        )


@pytest.mark.unit
def test_split_streaming_units_prefers_japanese_phrase_boundaries() -> None:
    assert split_streaming_units("うん、わかった。少し待ってね。") == [
        "うん、わかった。",
        "少し待ってね。",
    ]
    assert split_streaming_units("これは少し長い文なので途中で区切って返す", max_chars=10) == [
        "これは少し長い文なの",
        "で途中で区切って返す",
    ]


@pytest.mark.unit
async def test_irodori_mlx_stream_backend_yields_each_phrase_as_audio_chunk() -> None:
    fake_model = FakeIrodoriStreamModel()
    backend = IrodoriMLXStreamBackend(loaded_model=fake_model, voice="none")

    chunks = [
        chunk
        async for chunk in backend.synthesize(
            TTSInput(text="うん、わかった。少し待ってね。", style="happy")
        )
    ]

    assert [chunk.sequence for chunk in chunks] == [0, 1]
    assert [chunk.is_last for chunk in chunks] == [False, True]
    assert all(chunk.data.startswith(b"RIFF") for chunk in chunks)
    assert [call["text"] for call in fake_model.calls] == [
        "うん、わかった。",
        "少し待ってね。",
    ]
    assert all(call["seconds"] is not None for call in fake_model.calls)
    assert all(call["num_steps"] == 6 for call in fake_model.calls)
    assert all(call["t_schedule_mode"] == "sway" for call in fake_model.calls)


@pytest.mark.unit
def test_tts_factory_creates_irodori_mlx_stream_backend() -> None:
    backend = create_tts_backend(
        BackendSpec(
            name="irodori_mlx_stream",
            type="irodori_mlx_stream",
            model="mlx-community/Irodori-TTS-500M-v3-8bit",
            voice="none",
        )
    )

    assert isinstance(backend, IrodoriMLXStreamBackend)
