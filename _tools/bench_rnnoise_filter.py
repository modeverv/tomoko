from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.edge.pipeline.stt_gate import audio_signal_metrics  # noqa: E402


@dataclass(frozen=True, slots=True)
class RnnoiseBenchResult:
    input_path: str
    output_path: str
    model_path: str
    duration_ms: float
    elapsed_ms: float
    cpu_ms_per_audio_sec: float
    input_metrics: dict[str, float]
    output_metrics: dict[str, float]


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio, sample_rate


def run_rnnoise_filter(
    *,
    input_path: Path,
    output_path: Path,
    model_path: Path,
) -> float:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_path),
            "-af",
            f"arnndn=m={model_path}",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(output_path),
        ],
        check=True,
    )
    return (time.perf_counter() - started) * 1000


def bench_rnnoise(
    *,
    input_path: Path,
    output_path: Path,
    model_path: Path,
    repeat: int,
) -> RnnoiseBenchResult:
    elapsed_values = [
        run_rnnoise_filter(
            input_path=input_path,
            output_path=output_path,
            model_path=model_path,
        )
        for _ in range(repeat)
    ]
    input_audio, input_rate = read_wav(input_path)
    output_audio, output_rate = read_wav(output_path)
    if input_rate != output_rate:
        raise ValueError(f"sample rate mismatch: input={input_rate} output={output_rate}")
    duration_sec = max(len(input_audio) / input_rate, 1e-9)
    elapsed_ms = sum(elapsed_values) / len(elapsed_values)
    return RnnoiseBenchResult(
        input_path=str(input_path),
        output_path=str(output_path),
        model_path=str(model_path),
        duration_ms=len(input_audio) * 1000 / input_rate,
        elapsed_ms=elapsed_ms,
        cpu_ms_per_audio_sec=elapsed_ms / duration_sec,
        input_metrics=asdict(audio_signal_metrics(input_audio, input_rate)),
        output_metrics=asdict(audio_signal_metrics(output_audio, output_rate)),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark FFmpeg arnndn/RNNoise.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--model", type=Path, default=Path("work/rnnoise-models/std.rnnn"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()

    output_path = args.output
    if output_path is None:
        output_path = (
            Path("work/noise-filter-experiments")
            / args.input.stem
            / "rnnoise_arnndn_std.wav"
        )
    result = bench_rnnoise(
        input_path=args.input,
        output_path=output_path,
        model_path=args.model,
        repeat=args.repeat,
    )
    summary_path = output_path.with_suffix(".json")
    summary_path.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    print(f"JSON: {summary_path}")


if __name__ == "__main__":
    main()
