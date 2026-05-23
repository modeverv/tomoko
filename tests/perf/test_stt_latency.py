from __future__ import annotations

import os
import shutil
import subprocess
import time
import wave
from datetime import UTC, datetime
from importlib.util import find_spec
from pathlib import Path

import numpy as np
import pytest

from server.edge.pipeline.stt import create_stt_transcriber
from server.shared.config import NodeConfig
from server.shared.models import SpeechSegment

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.perf
@pytest.mark.parametrize(
    "backend_name",
    os.environ.get(
        "TOMOKO_STT_BENCH_BACKENDS",
        "local_whisper_small,local_whisper_mlx_small",
    ).split(","),
)
async def test_stt_backend_latency(backend_name: str, tmp_path: Path) -> None:
    backend_name = backend_name.strip()
    if not backend_name:
        pytest.skip("empty backend name")

    config = NodeConfig.load(ROOT / "config" / "central_realtime.toml")
    spec = config.backends[backend_name]
    if spec.type == "mlx_whisper" and find_spec("mlx_whisper") is None:
        pytest.skip("mlx-whisper is not installed")

    audio = _make_sample_audio(tmp_path)
    segment = SpeechSegment(
        audio=audio,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        device_id="perf",
        vad_confidence=1.0,
    )
    transcriber = create_stt_transcriber(spec)

    warm_start = time.perf_counter()
    warm = await transcriber.transcribe(segment)
    warm_ms = (time.perf_counter() - warm_start) * 1000

    measured_start = time.perf_counter()
    measured = await transcriber.transcribe(segment)
    measured_ms = (time.perf_counter() - measured_start) * 1000

    print(
        f"STT backend={backend_name} type={spec.type} model={spec.model} "
        f"warm_ms={warm_ms:.1f} measured_ms={measured_ms:.1f} "
        f"warm_text={warm.text!r} measured_text={measured.text!r}"
    )
    assert measured.text


def _make_sample_audio(tmp_path: Path) -> np.ndarray:
    if shutil.which("say") is None:
        pytest.skip("macOS say command is required for local STT perf sample")

    wav_path = tmp_path / "stt-sample.wav"
    subprocess.run(
        [
            "say",
            "-v",
            "Kyoko",
            "--data-format=LEI16@16000",
            "-o",
            str(wav_path),
            "ともこ、さんたすさんは、いくつですか。",
        ],
        check=True,
    )
    with wave.open(str(wav_path), "rb") as wav:
        frames = wav.readframes(wav.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        if wav.getnchannels() != 1:
            audio = audio.reshape(-1, wav.getnchannels()).mean(axis=1)
    return audio
