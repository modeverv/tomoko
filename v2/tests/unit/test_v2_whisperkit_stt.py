from __future__ import annotations

import json
import wave
from datetime import timedelta
from pathlib import Path

import pytest

from server.audio import stt as stt_module
from server.audio.stt import (
    WHISPERKIT_LARGE_V3_TURBO_MODEL,
    ArgmaxWhisperKitStreamingBackend,
    StreamingSttEvent,
    create_default_stt_backend,
)
from server.shared.models import AudioSpeechSegment, utc_now

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_argmax_whisperkit_final_uses_large_v3_turbo_and_cpu_ne(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_whisperkit_env(monkeypatch)
    observed_args: list[list[str]] = []
    observed_audio_paths: list[Path] = []

    def fake_run(args: list[str], **_kwargs: object) -> object:
        observed_args.append(args)
        audio_path = Path(args[args.index("--audio-path") + 1])
        observed_audio_paths.append(audio_path)
        with wave.open(str(audio_path), "rb") as wav:
            assert wav.getframerate() == 16000
            assert wav.getnchannels() == 1
            assert wav.getnframes() == 2

        class Completed:
            stdout = json.dumps({"text": "こんにちは"})

        return Completed()

    monkeypatch.setattr(stt_module.subprocess, "run", fake_run)
    backend = ArgmaxWhisperKitStreamingBackend(command="/bin/echo")

    events = [event async for event in backend.transcribe_stream(_segment())]

    assert events == [StreamingSttEvent("こんにちは", True, 1.0)]
    args = observed_args[0]
    assert args[:2] == ["/bin/echo", "transcribe"]
    assert ["--model", WHISPERKIT_LARGE_V3_TURBO_MODEL] == args[
        args.index("--model") : args.index("--model") + 2
    ]
    assert ["--audio-encoder-compute-units", "cpuAndNeuralEngine"] == args[
        args.index("--audio-encoder-compute-units") : args.index("--audio-encoder-compute-units")
        + 2
    ]
    assert ["--text-decoder-compute-units", "cpuAndNeuralEngine"] == args[
        args.index("--text-decoder-compute-units") : args.index("--text-decoder-compute-units")
        + 2
    ]
    assert "--stream-simulated" not in args
    assert observed_audio_paths
    assert not observed_audio_paths[0].exists()


@pytest.mark.asyncio
async def test_argmax_whisperkit_partial_uses_cli_stream_simulated_and_suppresses_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_whisperkit_env(monkeypatch)
    observed_args: list[list[str]] = []
    outputs = iter(["途中", "途中", "続き"])

    def fake_run(args: list[str], **_kwargs: object) -> object:
        observed_args.append(args)
        assert "--stream-simulated" in args
        audio_path = Path(args[args.index("--audio-path") + 1])
        with wave.open(str(audio_path), "rb") as wav:
            assert wav.getframerate() == 1000

        class Completed:
            stdout = next(outputs)

        return Completed()

    monkeypatch.setattr(stt_module.subprocess, "run", fake_run)
    backend = ArgmaxWhisperKitStreamingBackend(
        command="/bin/echo",
        stream_min_audio_ms=200,
        stream_interval_ms=100,
    )

    first = await backend.process_stream_chunk(
        (0.2,) * 100,
        sample_rate=1000,
        started_at_ms=1000.0,
    )
    second = await backend.process_stream_chunk(
        (0.2,) * 100,
        sample_rate=1000,
        started_at_ms=1100.0,
    )
    duplicate = await backend.process_stream_chunk(
        (0.2,) * 100,
        sample_rate=1000,
        started_at_ms=1200.0,
    )
    third = await backend.process_stream_chunk(
        (0.2,) * 100,
        sample_rate=1000,
        started_at_ms=1300.0,
    )

    assert first is None
    assert second == StreamingSttEvent("途中", False, 0.82)
    assert duplicate is None
    assert third == StreamingSttEvent("続き", False, 0.82)
    assert len(observed_args) == 3


def test_default_stt_backend_is_whisperkit_large_v3_turbo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_whisperkit_env(monkeypatch)
    monkeypatch.delenv("TOMOKO_V2_STT_BACKEND", raising=False)
    backend = create_default_stt_backend(command="/bin/echo")

    assert isinstance(backend, ArgmaxWhisperKitStreamingBackend)
    assert backend.model == WHISPERKIT_LARGE_V3_TURBO_MODEL
    assert backend.audio_encoder_compute_units == "cpuAndNeuralEngine"
    assert backend.text_decoder_compute_units == "cpuAndNeuralEngine"


def _segment() -> AudioSpeechSegment:
    now = utc_now()
    return AudioSpeechSegment(
        samples=(0.5, -0.5),
        sample_rate=16000,
        started_at=now - timedelta(milliseconds=1),
        ended_at=now,
    )


def _clear_whisperkit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "TOMOKO_V2_WHISPERKIT_MODEL",
        "TOMOKO_V2_WHISPERKIT_MODEL_PATH",
        "TOMOKO_V2_WHISPERKIT_AUDIO_ENCODER_COMPUTE_UNITS",
        "TOMOKO_V2_WHISPERKIT_TEXT_DECODER_COMPUTE_UNITS",
        "TOMOKO_V2_WHISPERKIT_PROMPT",
    ):
        monkeypatch.delenv(name, raising=False)
