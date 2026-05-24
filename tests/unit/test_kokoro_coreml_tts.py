from __future__ import annotations

import io
import wave

import numpy as np
import pytest

from server.shared.config import BackendSpec
from server.shared.inference.tts import create_tts_backend
from server.shared.inference.tts.kokoro_coreml import KokoroCoreMLBackend
from server.shared.models import TTSInput


class FakeStreamingCoreMLTTS:
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
        yield np.array([0.0, 0.5], dtype=np.float32)
        yield np.array([-0.5], dtype=np.float32)


@pytest.mark.unit
async def test_kokoro_coreml_backend_streams_wav_chunks_from_python_generator() -> None:
    fake_tts = FakeStreamingCoreMLTTS()
    backend = KokoroCoreMLBackend(tts=fake_tts, sample_rate=24000, voice="jf_alpha")

    chunks = [
        chunk
        async for chunk in backend.synthesize(TTSInput(text="こんにちは。", style="happy"))
    ]

    assert len(chunks) == 2
    assert chunks[0].data.startswith(b"RIFF")
    with wave.open(io.BytesIO(chunks[0].data), "rb") as wav:
        assert wav.getframerate() == 24000
        assert wav.getnchannels() == 1
        assert wav.getnframes() == 2
    assert fake_tts.calls == [
        {
            "text": "こんにちは。",
            "voice": "jf_alpha",
            "speed": 1.05,
            "sample_rate": 24000,
            "language": "ja",
        }
    ]


@pytest.mark.unit
def test_tts_factory_creates_kokoro_coreml_backend() -> None:
    backend = create_tts_backend(
        BackendSpec(
            name="kokoro_coreml",
            type="kokoro_coreml",
            model_path="models/kokoro-coreml",
            command="kokoro",
            voice="jf_alpha",
            sample_rate=24000,
            streaming=True,
        )
    )

    assert isinstance(backend, KokoroCoreMLBackend)
    assert backend.command == "kokoro"
    assert backend.streaming is True
