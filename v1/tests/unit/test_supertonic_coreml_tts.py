from __future__ import annotations

import io
import wave
from pathlib import Path

import numpy as np
import pytest

from server.shared.config import BackendSpec
from server.shared.inference.tts import create_tts_backend
from server.shared.inference.tts.supertonic_coreml import SupertonicCoreMLBackend
from server.shared.models import TTSInput


class FakeSupertonicTTS:
    sample_rate = 24000

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def synthesize(
        self,
        text: str,
        voice_style_path: Path,
        *,
        lang: str,
        total_step: int,
        speed: float,
    ):
        self.calls.append(
            {
                "text": text,
                "voice_style_path": voice_style_path,
                "lang": lang,
                "total_step": total_step,
                "speed": speed,
            }
        )
        return np.array([0.0, 0.5, -0.5], dtype=np.float32), 0.25


@pytest.mark.unit
async def test_supertonic_coreml_backend_returns_single_wav_chunk() -> None:
    fake_tts = FakeSupertonicTTS()
    voice_style_path = Path("models/supertonic-3-coreml/voice_styles/F1.json")
    backend = SupertonicCoreMLBackend(
        tts=fake_tts,
        model_dir=Path("models/supertonic-3-coreml"),
        voice="F1",
        lang="ja",
        total_step=8,
        speed=1.05,
    )

    chunks = [
        chunk
        async for chunk in backend.synthesize(TTSInput(text="こんにちは。", style="happy"))
    ]

    assert len(chunks) == 1
    assert chunks[0].sequence == 0
    assert chunks[0].is_last is True
    assert chunks[0].data.startswith(b"RIFF")
    with wave.open(io.BytesIO(chunks[0].data), "rb") as wav:
        assert wav.getframerate() == 24000
        assert wav.getnchannels() == 1
        assert wav.getnframes() == 3
    assert fake_tts.calls == [
        {
            "text": "こんにちは。",
            "voice_style_path": voice_style_path,
            "lang": "ja",
            "total_step": 8,
            "speed": 1.05,
        }
    ]


@pytest.mark.unit
def test_tts_factory_creates_supertonic_coreml_backend() -> None:
    backend = create_tts_backend(
        BackendSpec(
            name="supertonic_coreml_f1",
            type="supertonic_coreml",
            model_path="models/supertonic-3-coreml",
            voice="F1",
            sample_rate=24000,
            max_latency_ms=200,
        )
    )

    assert isinstance(backend, SupertonicCoreMLBackend)
    assert backend.name == "supertonic_coreml"
    assert backend.voice == "F1"
    assert backend.model_dir == Path("models/supertonic-3-coreml")
