from __future__ import annotations

import argparse
import json
import math
import sys
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@dataclass(frozen=True, slots=True)
class AudioMetrics:
    duration_ms: float
    sample_count: int
    rms_db: float
    peak_db: float
    active_frame_ratio: float
    active_frame_rms_db_p50: float
    active_frame_rms_db_p95: float


@dataclass(frozen=True, slots=True)
class FilterResult:
    name: str
    elapsed_ms: float
    cpu_ms_per_audio_sec: float
    kept_ratio: float
    metrics: AudioMetrics
    output_path: str


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    pcm = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)
    return pcm, sample_rate


def write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = np.clip(audio.astype(np.float32, copy=False), -1.0, 1.0)
    pcm = (samples * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def db_from_rms(rms: float) -> float:
    if rms <= 0.0:
        return -120.0
    return 20.0 * math.log10(rms)


def audio_metrics(
    audio: np.ndarray,
    sample_rate: int,
    *,
    frame_ms: int = 32,
    active_threshold_db: float = -55.0,
) -> AudioMetrics:
    frame_size = max(1, int(sample_rate * frame_ms / 1000))
    frame_db = frame_rms_db(audio, frame_size)
    active = frame_db >= active_threshold_db
    active_values = frame_db[active]
    if active_values.size == 0:
        p50 = -120.0
        p95 = -120.0
    else:
        p50 = float(np.percentile(active_values, 50))
        p95 = float(np.percentile(active_values, 95))
    return AudioMetrics(
        duration_ms=len(audio) * 1000 / sample_rate,
        sample_count=len(audio),
        rms_db=db_from_rms(float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0),
        peak_db=db_from_rms(float(np.max(np.abs(audio))) if audio.size else 0.0),
        active_frame_ratio=float(np.mean(active)) if frame_db.size else 0.0,
        active_frame_rms_db_p50=p50,
        active_frame_rms_db_p95=p95,
    )


def frame_rms_db(audio: np.ndarray, frame_size: int) -> np.ndarray:
    if audio.size == 0:
        return np.zeros(0, dtype=np.float32)
    frame_count = math.ceil(len(audio) / frame_size)
    padded = np.pad(audio, (0, frame_count * frame_size - len(audio)))
    frames = padded.reshape(frame_count, frame_size)
    rms = np.sqrt(np.mean(np.square(frames), axis=1))
    return np.array([db_from_rms(float(value)) for value in rms], dtype=np.float32)


def hard_segment_gate(
    audio: np.ndarray,
    sample_rate: int,
    threshold_db: float,
) -> tuple[np.ndarray, float]:
    metrics = audio_metrics(audio, sample_rate)
    if metrics.rms_db < threshold_db:
        return np.zeros_like(audio), 0.0
    return audio.copy(), 1.0


def frame_rms_gate(
    audio: np.ndarray,
    sample_rate: int,
    *,
    threshold_db: float,
    frame_ms: int = 32,
    hangover_ms: int = 160,
) -> tuple[np.ndarray, float]:
    frame_size = max(1, int(sample_rate * frame_ms / 1000))
    frame_db = frame_rms_db(audio, frame_size)
    keep = frame_db >= threshold_db
    hangover_frames = max(0, int(hangover_ms / frame_ms))
    if hangover_frames:
        expanded = keep.copy()
        for index, value in enumerate(keep):
            if value:
                start = max(0, index - 1)
                end = min(len(keep), index + hangover_frames + 1)
                expanded[start:end] = True
        keep = expanded

    frame_count = len(keep)
    padded = np.pad(audio, (0, frame_count * frame_size - len(audio)))
    frames = padded.reshape(frame_count, frame_size).copy()
    frames[~keep] = 0.0
    filtered = frames.reshape(-1)[: len(audio)]
    return filtered, float(np.count_nonzero(keep) / len(keep)) if len(keep) else 0.0


def spectral_gate(
    audio: np.ndarray,
    noise: np.ndarray,
    sample_rate: int,
    *,
    fft_size: int = 512,
    hop_size: int = 128,
    over_subtract: float = 1.5,
    min_gain: float = 0.15,
) -> tuple[np.ndarray, float]:
    if audio.size == 0:
        return audio.copy(), 0.0
    noise_profile = _noise_profile(noise, fft_size, hop_size)
    frames = _frame_audio(audio, fft_size, hop_size)
    window = np.hanning(fft_size).astype(np.float32)
    output = np.zeros((frames.shape[0] - 1) * hop_size + fft_size, dtype=np.float32)
    norm = np.zeros_like(output)
    gains: list[float] = []
    for index, frame in enumerate(frames):
        spectrum = np.fft.rfft(frame * window)
        magnitude = np.abs(spectrum)
        gain = np.maximum(
            min_gain,
            (magnitude - over_subtract * noise_profile) / np.maximum(magnitude, 1e-8),
        )
        gains.append(float(np.mean(gain)))
        filtered = np.fft.irfft(spectrum * gain, n=fft_size).astype(np.float32)
        start = index * hop_size
        output[start : start + fft_size] += filtered * window
        norm[start : start + fft_size] += window * window
    valid = norm > 1e-4
    output[valid] /= norm[valid]
    output[~valid] = 0.0
    return np.clip(output[: len(audio)], -1.0, 1.0), float(np.mean(gains)) if gains else 0.0


def _noise_profile(noise: np.ndarray, fft_size: int, hop_size: int) -> np.ndarray:
    frames = _frame_audio(noise, fft_size, hop_size)
    window = np.hanning(fft_size).astype(np.float32)
    mags = np.abs(np.fft.rfft(frames * window[None, :], axis=1))
    return np.median(mags, axis=0)


def _frame_audio(audio: np.ndarray, fft_size: int, hop_size: int) -> np.ndarray:
    if audio.size == 0:
        return np.zeros((1, fft_size), dtype=np.float32)
    frame_count = max(1, math.ceil((len(audio) - fft_size) / hop_size) + 1)
    total = (frame_count - 1) * hop_size + fft_size
    padded = np.pad(audio.astype(np.float32, copy=False), (0, max(0, total - len(audio))))
    return np.stack(
        [padded[index * hop_size : index * hop_size + fft_size] for index in range(frame_count)]
    )


def benchmark_filter(
    name: str,
    audio: np.ndarray,
    noise: np.ndarray,
    sample_rate: int,
    output_path: Path,
    *,
    repeat: int,
) -> FilterResult:
    filtered = audio
    kept_ratio = 1.0
    start = time.perf_counter()
    for _ in range(repeat):
        if name.startswith("segment_gate_"):
            threshold = float(name.removeprefix("segment_gate_").removesuffix("db"))
            filtered, kept_ratio = hard_segment_gate(audio, sample_rate, threshold)
        elif name.startswith("frame_gate_"):
            threshold = float(name.removeprefix("frame_gate_").removesuffix("db"))
            filtered, kept_ratio = frame_rms_gate(audio, sample_rate, threshold_db=threshold)
        elif name == "spectral_gate":
            filtered, kept_ratio = spectral_gate(audio, noise, sample_rate)
        else:
            raise ValueError(f"unknown filter: {name}")
    elapsed_ms = (time.perf_counter() - start) * 1000 / repeat
    write_wav(output_path, filtered, sample_rate)
    audio_sec = max(len(audio) / sample_rate, 1e-9)
    return FilterResult(
        name=name,
        elapsed_ms=elapsed_ms,
        cpu_ms_per_audio_sec=elapsed_ms / audio_sec,
        kept_ratio=kept_ratio,
        metrics=audio_metrics(filtered, sample_rate),
        output_path=str(output_path),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark simple STT pre-filters.")
    parser.add_argument("--noise", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("work/noise-filter-experiments"))
    parser.add_argument("--repeat", type=int, default=50)
    args = parser.parse_args()

    noise, noise_rate = read_wav(args.noise)
    audio, sample_rate = read_wav(args.input)
    if noise_rate != sample_rate:
        raise ValueError(f"sample rate mismatch: noise={noise_rate} input={sample_rate}")

    run_dir = args.output_dir / args.input.stem
    run_dir.mkdir(parents=True, exist_ok=True)
    filters = [
        "segment_gate_-55db",
        "segment_gate_-50db",
        "segment_gate_-45db",
        "frame_gate_-60db",
        "frame_gate_-55db",
        "frame_gate_-50db",
        "frame_gate_-45db",
        "spectral_gate",
    ]
    results = [
        benchmark_filter(
            name,
            audio,
            noise,
            sample_rate,
            run_dir / f"{name}.wav",
            repeat=args.repeat,
        )
        for name in filters
    ]
    payload = {
        "noise_path": str(args.noise),
        "input_path": str(args.input),
        "sample_rate": sample_rate,
        "input_metrics": asdict(audio_metrics(audio, sample_rate)),
        "noise_metrics": asdict(audio_metrics(noise, sample_rate)),
        "results": [asdict(result) for result in results],
    }
    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"JSON: {summary_path}")
    print("| filter | cpu_ms/audio_sec | kept | rms_db | peak_db | output |")
    print("|---|---:|---:|---:|---:|---|")
    for result in results:
        print(
            "| "
            f"{result.name} | "
            f"{result.cpu_ms_per_audio_sec:.3f} | "
            f"{result.kept_ratio:.3f} | "
            f"{result.metrics.rms_db:.1f} | "
            f"{result.metrics.peak_db:.1f} | "
            f"{result.output_path} |"
        )


if __name__ == "__main__":
    main()
