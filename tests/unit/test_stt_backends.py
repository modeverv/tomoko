from __future__ import annotations

import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pytest

from server.edge.pipeline.stt import MlxWhisperSTT, create_stt_transcriber
from server.shared.config import BackendSpec
from server.shared.models import SpeechSegment


@pytest.mark.unit
def test_create_stt_transcriber_supports_mlx_whisper() -> None:
    transcriber = create_stt_transcriber(
        BackendSpec(
            name="local_whisper_mlx_small",
            type="mlx_whisper",
            model="mlx-community/whisper-small-mlx",
            streaming=True,
            stream_interval_ms=500,
            stream_min_audio_ms=500,
        )
    )

    assert isinstance(transcriber, MlxWhisperSTT)
    assert transcriber.model_name == "mlx-community/whisper-small-mlx"
    assert transcriber.streaming is True


@pytest.mark.unit
async def test_mlx_whisper_transcribes_via_temp_wav(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_transcribe(audio_path: str, **kwargs: object) -> dict[str, str]:
        calls.append({"audio_path": audio_path, **kwargs})
        return {"text": "ともこ、聞こえます"}

    monkeypatch.setitem(
        sys.modules,
        "mlx_whisper",
        SimpleNamespace(transcribe=fake_transcribe),
    )
    transcriber = MlxWhisperSTT(model_name="mlx-community/whisper-small-mlx")
    segment = SpeechSegment(
        audio=np.zeros(1600, dtype=np.float32),
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        device_id="local",
        vad_confidence=0.9,
    )

    transcript = await transcriber.transcribe(segment)

    assert transcript.text == "ともこ、聞こえます"
    assert calls[0]["path_or_hf_repo"] == "mlx-community/whisper-small-mlx"
    assert calls[0]["language"] == "ja"
    assert calls[0]["initial_prompt"] == "ともこ"


@pytest.mark.unit
async def test_mlx_whisper_streaming_returns_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_transcribe(audio_path: str, **kwargs: object) -> dict[str, str]:
        del audio_path, kwargs
        return {"text": "途中です"}

    monkeypatch.setitem(
        sys.modules,
        "mlx_whisper",
        SimpleNamespace(transcribe=fake_transcribe),
    )
    transcriber = MlxWhisperSTT(
        streaming=True,
        stream_interval_ms=500,
        stream_min_audio_ms=500,
    )

    partial = await transcriber.process_stream_chunk(
        np.ones(2, dtype=np.float32),
        device_id="local",
        sample_rate=4,
    )

    assert partial is not None
    assert partial.text == "途中です"
    assert partial.is_final is False


@pytest.mark.unit
async def test_mlx_whisper_streaming_suppresses_duplicate_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_transcribe(audio_path: str, **kwargs: object) -> dict[str, str]:
        del audio_path, kwargs
        return {"text": "途中です"}

    monkeypatch.setitem(
        sys.modules,
        "mlx_whisper",
        SimpleNamespace(transcribe=fake_transcribe),
    )
    transcriber = MlxWhisperSTT(
        streaming=True,
        stream_interval_ms=500,
        stream_min_audio_ms=500,
    )

    first = await transcriber.process_stream_chunk(
        np.ones(2, dtype=np.float32),
        device_id="local",
        sample_rate=4,
    )
    second = await transcriber.process_stream_chunk(
        np.ones(2, dtype=np.float32),
        device_id="local",
        sample_rate=4,
    )

    assert first is not None
    assert second is None
