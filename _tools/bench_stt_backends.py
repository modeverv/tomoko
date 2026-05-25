from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time
import wave
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.edge.pipeline.stt import create_stt_transcriber  # noqa: E402
from server.shared.config import BackendSpec, NodeConfig  # noqa: E402
from server.shared.models import SpeechSegment  # noqa: E402

DEFAULT_BACKENDS = "local_whisper_mlx_small,local_whisperkit_serve_small"
DEFAULT_TEXT = "ともこ、さんたすさんは、いくつですか。"


@dataclass(frozen=True, slots=True)
class Measurement:
    elapsed_ms: float
    text: str


@dataclass(frozen=True, slots=True)
class MeasurementSummary:
    avg_ms: float
    min_ms: float
    max_ms: float


@dataclass(frozen=True, slots=True)
class BackendBenchResult:
    backend: str
    type: str
    model: str | None
    command: str | None
    streaming: bool
    warmup_ms: float
    warmup_text: str
    avg_ms: float
    min_ms: float
    max_ms: float
    runs: list[Measurement]


def parse_backend_names(value: str) -> list[str]:
    return [name.strip() for name in value.split(",") if name.strip()]


def summarize_measurements(measurements: list[Measurement]) -> MeasurementSummary:
    if not measurements:
        raise ValueError("at least one measurement is required")
    values = [measurement.elapsed_ms for measurement in measurements]
    return MeasurementSummary(
        avg_ms=sum(values) / len(values),
        min_ms=min(values),
        max_ms=max(values),
    )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark configured STT backends such as MLX Whisper "
            "and WhisperKit CoreML serve."
        ),
    )
    parser.add_argument("--config", default="config/central_realtime.toml")
    parser.add_argument(
        "--backends",
        default=DEFAULT_BACKENDS,
        help="Comma-separated backend names from config/*.toml.",
    )
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument(
        "--audio-file",
        default=None,
        help="Optional mono/stereo WAV input. When omitted, macOS say generates a 16kHz sample.",
    )
    parser.add_argument(
        "--output",
        default="logs/stt-mlx-coreml-bench.json",
        help="JSON summary path.",
    )
    args = parser.parse_args()

    results = await bench_from_args(args)
    print_results(results)
    print(f"\nJSON: {args.output}")


async def bench_from_args(args: argparse.Namespace) -> list[BackendBenchResult]:
    if args.runs < 1:
        raise ValueError("--runs must be >= 1")

    config_path = Path(args.config)
    config = NodeConfig.load(config_path)
    backend_names = parse_backend_names(args.backends)
    if not backend_names:
        raise ValueError("--backends must include at least one backend name")

    audio_path = Path(args.audio_file) if args.audio_file else _make_sample_audio(args.text)
    audio = _read_wav_float32(audio_path)
    segment = _make_segment(audio)

    results: list[BackendBenchResult] = []
    for backend_name in backend_names:
        spec = config.backends.get(backend_name)
        if spec is None:
            raise KeyError(f"unknown backend in {config_path}: {backend_name}")
        result = await bench_backend(spec, segment, args.runs)
        results.append(result)

    write_json_summary(
        Path(args.output),
        config_path=config_path,
        audio_path=audio_path,
        sample_text=args.text,
        results=results,
    )
    return results


async def bench_backend(
    spec: BackendSpec,
    segment: SpeechSegment,
    runs: int,
) -> BackendBenchResult:
    transcriber = create_stt_transcriber(spec)
    try:
        warmup = await _measure_once(transcriber, segment)
        measurements = [await _measure_once(transcriber, segment) for _ in range(runs)]
    finally:
        close = getattr(transcriber, "close", None)
        if close is not None:
            await close()

    summary = summarize_measurements(measurements)
    return BackendBenchResult(
        backend=spec.name,
        type=spec.type,
        model=spec.model_path or spec.model,
        command=spec.command,
        streaming=spec.streaming,
        warmup_ms=warmup.elapsed_ms,
        warmup_text=warmup.text,
        avg_ms=summary.avg_ms,
        min_ms=summary.min_ms,
        max_ms=summary.max_ms,
        runs=measurements,
    )


async def _measure_once(transcriber: Any, segment: SpeechSegment) -> Measurement:
    start = time.perf_counter()
    transcript = await transcriber.transcribe(segment)
    elapsed_ms = (time.perf_counter() - start) * 1000
    return Measurement(elapsed_ms=elapsed_ms, text=transcript.text)


def print_results(results: list[BackendBenchResult]) -> None:
    print("| backend | type | model | streaming | warmup_ms | avg_ms | min_ms | max_ms | text |")
    print("|---|---|---|---:|---:|---:|---:|---:|---|")
    for result in results:
        text = result.runs[-1].text.replace("|", "\\|") if result.runs else ""
        print(
            f"| {result.backend} | {result.type} | {result.model or ''} | "
            f"{str(result.streaming).lower()} | {result.warmup_ms:.1f} | "
            f"{result.avg_ms:.1f} | {result.min_ms:.1f} | {result.max_ms:.1f} | {text} |"
        )


def write_json_summary(
    path: Path,
    *,
    config_path: Path,
    audio_path: Path,
    sample_text: str,
    results: list[BackendBenchResult],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "config": str(config_path),
        "audio_path": str(audio_path),
        "sample_text": sample_text,
        "results": [asdict(result) for result in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _make_sample_audio(text: str) -> Path:
    if shutil.which("say") is None:
        raise RuntimeError("macOS say command is required when --audio-file is omitted")

    output_dir = ROOT / "logs" / "stt-bench"
    output_dir.mkdir(parents=True, exist_ok=True)
    wav_path = output_dir / "sample.wav"
    subprocess.run(
        [
            "say",
            "-v",
            "Kyoko",
            "--data-format=LEI16@16000",
            "-o",
            str(wav_path),
            text,
        ],
        check=True,
    )
    return wav_path


def _read_wav_float32(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wav:
        frames = wav.readframes(wav.getnframes())
        if wav.getsampwidth() != 2:
            raise ValueError(f"expected 16-bit WAV: {path}")
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        if wav.getnchannels() != 1:
            audio = audio.reshape(-1, wav.getnchannels()).mean(axis=1)
    return audio.astype(np.float32, copy=False)


def _make_segment(audio: np.ndarray) -> SpeechSegment:
    now = datetime.now(UTC)
    return SpeechSegment(
        audio=audio,
        started_at=now,
        ended_at=now,
        device_id="bench",
        vad_confidence=1.0,
    )


if __name__ == "__main__":
    asyncio.run(main())
