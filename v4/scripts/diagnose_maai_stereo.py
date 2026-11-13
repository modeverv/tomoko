"""Bounded stereo contrast on synthetic runs; real models are loaded only by the CLI.

Require HF_HUB_OFFLINE=1 before starting Python. Compare identical microphone audio
with a zero or recorded system channel. Receiver WAV alignment is an approximation,
not an exact replay of the original sensor's scheduling. No policy is evaluated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import queue
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SAMPLE_RATE = 16000
OUTPUT_RATE = 24000
MAX_RUNS = 4
MAX_SECONDS = 120.0


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _read_mono(path: Path, rate: int) -> np.ndarray:
    audio, actual_rate = sf.read(path, dtype="float32", always_2d=True)
    if actual_rate != rate:
        raise ValueError(f"{path.name}: sample rate must be {rate}")
    if audio.shape[1] != 1 or not np.isfinite(audio).all():
        raise ValueError(f"{path.name}: finite mono audio required")
    return audio[:, 0]


def _align(directory: Path, start: int, limit: int) -> tuple[np.ndarray, np.ndarray, dict]:
    mic = _read_mono(directory / "input.wav", SAMPLE_RATE)
    system = _read_mono(directory / "output.wav", OUTPUT_RATE)
    end = max(len(mic), math.ceil(len(system) * SAMPLE_RATE / OUTPUT_RATE))
    n = min(limit, end - start)
    if n <= 0:
        raise ValueError("requested window contains no recorded samples")
    aligned_mic = np.zeros(n, dtype=np.float32)
    count = max(0, min(n, len(mic) - start))
    aligned_mic[:count] = mic[start:start + count]
    aligned_system = np.zeros(n, dtype=np.float32)
    positions = (start + np.arange(n, dtype=np.float64)) * OUTPUT_RATE / SAMPLE_RATE
    valid = positions < len(system)
    if valid.any():
        # Preserve sample times; hold the last sample until the file's end, then pad zero.
        aligned_system[valid] = np.interp(positions[valid], np.arange(len(system)), system)
    metadata = {
        "input_samples": len(mic), "output_samples": len(system),
        "input_rate": SAMPLE_RATE, "output_rate": OUTPUT_RATE,
        "start_s": start / SAMPLE_RATE, "end_s": (start + n) / SAMPLE_RATE,
        "window_samples": n,
    }
    return aligned_mic, aligned_system, metadata


def _make_cached_model(mode: str) -> Any:
    # make_model imports torch/MaAI only when called; bc_det automatically selects Mimi.
    from scripts.maai_offline import make_model

    return make_model(mode, "jp", 10, "cpc")


def _numeric_frame(result: Mapping[str, Any], mode: str, at_s: float) -> dict:
    values = result.get(mode, result)
    fields = ("p_now", "p_future", "vad") if mode == "vap" else ("p_bc_det",)
    frame: dict[str, Any] = {"t": at_s}
    for key in fields:
        value = values.get(key)
        if key == "vad" and isinstance(value, Mapping):
            value = value.get("vad")
        if value is None or len(value) != 2:
            raise ValueError(f"model result requires stereo numeric {key}")
        pair = [float(v) for v in value]
        if not all(math.isfinite(v) for v in pair):
            raise ValueError(f"model result has nonfinite {key}")
        frame[key] = pair
    return frame


def _condition(
    model: Any, mic: np.ndarray, system: np.ndarray, mode: str, start: int,
) -> dict:
    before = time.perf_counter()
    model.reset_runtime_state()
    while True:
        try:
            model.result_dict_queue.get_nowait()
        except queue.Empty:
            break
    size = int(model.audio_samples_per_frame)
    if size <= 0:
        raise ValueError("model frame size must be positive")
    frames, process_times = [], []
    missing = 0
    for offset in range(0, len(mic), size):
        count = min(size, len(mic) - offset)
        ch1, ch2 = np.zeros(size, dtype=np.float32), np.zeros(size, dtype=np.float32)
        ch1[:count], ch2[:count] = mic[offset:offset + count], system[offset:offset + count]
        call_start = time.perf_counter()
        model.process(ch1, ch2)  # Both conditions remain stereo: ch1=user, ch2=system.
        process_times.append(time.perf_counter() - call_start)
        try:
            result = model.result_dict_queue.get_nowait()
        except queue.Empty as exc:
            if offset < 2 * size:  # Mimi may produce no result for its first two frames.
                missing += 1
                continue
            raise RuntimeError("model produced no frame after warmup") from exc
        if not model.result_dict_queue.empty():
            raise RuntimeError("model produced multiple results for one input frame")
        frames.append(_numeric_frame(result, mode, (start + offset + size) / SAMPLE_RATE))
    return {
        "frames": frames, "input_samples": len(mic),
        "processed_samples": len(process_times) * size, "frame_samples": size,
        "processed_frames": len(process_times), "result_frames": len(frames),
        "warmup_missing_frames": missing, "tail_padding_samples": (-len(mic)) % size,
        "processing_wall_s": time.perf_counter() - before,
        "process_wall_s": {
            "p50": float(np.percentile(process_times, 50)),
            "p99": float(np.percentile(process_times, 99)),
            "max": max(process_times),
        },
    }


def probe_runs(
    run_dirs: Sequence[Path], *, start_s: float = 0.0, duration_s: float = 45.0,
    mode: str = "vap", model_factory: Callable[[str], Any] | None = None,
) -> dict:
    """Read only explicitly non-private runs; return whitelisted numeric results and hashes.

    Wall measurements describe serial offline processing, not online response latency.
    Reset/drain precedes every condition. Loading and file alignment are timed separately
    from per-condition processing; the final incomplete model frame is zero-padded.
    """
    if os.environ.get("HF_HUB_OFFLINE") != "1":
        raise ValueError("HF_HUB_OFFLINE=1 must be set before starting this probe")
    if not 1 <= len(run_dirs) <= MAX_RUNS:
        raise ValueError(f"provide between 1 and {MAX_RUNS} explicit run directories")
    if (not math.isfinite(start_s) or start_s < 0 or not math.isfinite(duration_s)
            or not 0 < duration_s <= MAX_SECONDS or round(duration_s * SAMPLE_RATE) < 1):
        raise ValueError(f"window must start at >=0 and last >0 to {MAX_SECONDS} seconds")
    if mode not in {"vap", "bc_det"}:
        raise ValueError("mode must be vap or bc_det")
    directories = [Path(p) for p in run_dirs]
    # Validate every privacy flag before any WAV reads or model creation.
    for directory in directories:
        record = json.loads((directory / "run.json").read_text())
        if record.get("private") is not False:
            raise ValueError("run.json private must be explicitly false")
    sources = []
    for directory in directories:
        mic, system, metadata = _align(
            directory, round(start_s * SAMPLE_RATE), round(duration_s * SAMPLE_RATE),
        )
        metadata["sha256"] = {
            key: _sha256(directory / name) for key, name in (
                ("run_json", "run.json"), ("input_wav", "input.wav"), ("output_wav", "output.wav"),
            )
        }
        sources.append((mic, system, metadata))
    load_start = time.perf_counter()
    model = (model_factory or _make_cached_model)(mode)
    load_wall = time.perf_counter() - load_start
    runs = []
    try:
        for mic, system, metadata in sources:
            metadata["conditions"] = {
                "system_zero": _condition(
                    model, mic, np.zeros_like(system), mode, round(metadata["start_s"] * SAMPLE_RATE),
                ),
                "system_recorded": _condition(
                    model, mic, system, mode, round(metadata["start_s"] * SAMPLE_RATE),
                ),
            }
            runs.append(metadata)
    finally:
        model.stop(wait=True)
    return {
        "schema_version": 1, "mode": mode, "sample_rate": SAMPLE_RATE,
        "alignment": "receiver_timeline_approximation_not_original_sensor_replay",
        "channels": ["microphone", "system"], "model_load_wall_s": load_wall, "runs": runs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mode", choices=("vap", "bc_det"), default="vap")
    parser.add_argument("--start-s", type=float, default=0.0)
    parser.add_argument("--duration-s", type=float, default=45.0)
    args = parser.parse_args()
    if args.out.resolve() in {
        (directory / name).resolve()
        for directory in args.runs for name in ("run.json", "input.wav", "output.wav")
    }:
        parser.error("output must not overwrite source artifacts")
    try:
        report = probe_runs(args.runs, start_s=args.start_s, duration_s=args.duration_s, mode=args.mode)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
