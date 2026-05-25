from __future__ import annotations

import io
import wave

import numpy as np
import pytest

from server.shared.config import BackendSpec
from server.shared.inference.tts import create_tts_backend
from server.shared.inference.tts.kokoro_mlx import (
    KokoroMLXBackend,
    _install_pyopenjtalk_japanese_phonemizer,
)
from server.shared.models import TTSInput


class FakeKokoroTTS:
    def __init__(self, voices: list[str] | None = None) -> None:
        self._voices = voices or []
        self.calls: list[dict[str, object]] = []

    def list_voices(self) -> list[str]:
        return self._voices

    def generate_stream(
        self,
        text: str,
        *,
        voice: str,
        speed: float,
        sample_rate: int,
        language: str | None = None,
    ):
        self.calls.append(
            {
                "text": text,
                "voice": voice,
                "speed": speed,
                "sample_rate": sample_rate,
                "language": language,
            }
        )
        yield np.array([0.0, 0.5, -0.5], dtype=np.float32)
        yield np.array([0.25, -0.25], dtype=np.float32)


class FakeKokoroTTSWithoutVoiceList:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate_stream(
        self,
        text: str,
        *,
        voice: str,
        speed: float,
        sample_rate: int,
        language: str | None = None,
    ):
        self.calls.append(
            {
                "text": text,
                "voice": voice,
                "speed": speed,
                "sample_rate": sample_rate,
                "language": language,
            }
        )
        yield np.array([0.0, 0.5, -0.5], dtype=np.float32)
        yield np.array([0.25, -0.25], dtype=np.float32)


@pytest.mark.unit
async def test_kokoro_mlx_backend_streams_wav_chunks_from_numpy_audio() -> None:
    fake_tts = FakeKokoroTTSWithoutVoiceList()
    backend = KokoroMLXBackend(tts=fake_tts, sample_rate=24000)

    chunks = [
        chunk
        async for chunk in backend.synthesize(TTSInput(text="こんにちは。", style="sad"))
    ]

    assert len(chunks) == 2
    assert chunks[0].data.startswith(b"RIFF")
    assert chunks[1].data.startswith(b"RIFF")
    with wave.open(io.BytesIO(chunks[0].data), "rb") as wav:
        assert wav.getframerate() == 24000
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getnframes() == 3
    assert fake_tts.calls == [
        {
            "text": "こんにちは。",
            "voice": "jf_beta",
            "speed": 0.92,
            "sample_rate": 24000,
            "language": "ja",
        }
    ]


@pytest.mark.unit
async def test_kokoro_mlx_backend_falls_back_when_style_voice_is_missing() -> None:
    fake_tts = FakeKokoroTTS(voices=["jf_alpha", "jf_nezumi"])
    backend = KokoroMLXBackend(tts=fake_tts, voice="jf_alpha", sample_rate=24000)

    chunks = [
        chunk
        async for chunk in backend.synthesize(TTSInput(text="大丈夫。", style="sad"))
    ]

    assert len(chunks) == 2
    assert fake_tts.calls[0]["voice"] == "jf_alpha"
    assert fake_tts.calls[0]["language"] == "ja"


@pytest.mark.unit
def test_kokoro_mlx_installs_pyopenjtalk_phonemizer_for_japanese() -> None:
    class FakeConfig:
        vocab = {"k": 1, "o": 2, "ɴ": 3}

    class FakeTTS:
        _config = FakeConfig()

        def __init__(self) -> None:
            self._phonemizers = {}

    fake_tts = FakeTTS()

    _install_pyopenjtalk_japanese_phonemizer(fake_tts)

    assert fake_tts._phonemizers["ja"]._language == "ja"
    assert fake_tts._phonemizers["ja"]._g2p.version == "pyopenjtalk"


@pytest.mark.unit
def test_tts_factory_creates_kokoro_mlx_backend(monkeypatch) -> None:
    import sys
    import types

    class FakeKokoroTTSClass:
        @staticmethod
        def from_pretrained(model: str):
            return {"model": model}

    monkeypatch.setitem(
        sys.modules,
        "kokoro_mlx",
        types.SimpleNamespace(KokoroTTS=FakeKokoroTTSClass),
    )

    backend = create_tts_backend(
        BackendSpec(
            name="kokoro_mlx",
            type="kokoro_mlx",
            model="mlx-community/Kokoro-82M-bf16",
            voice="jf_alpha",
            sample_rate=24000,
        )
    )

    assert isinstance(backend, KokoroMLXBackend)
