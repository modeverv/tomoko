#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from make_model.model import HashRidgeSaturationModel


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        raise ValueError("values must not be empty")
    if percentile_value < 0.0 or percentile_value > 100.0:
        raise ValueError("percentile must be between 0 and 100")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (percentile_value / 100.0)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def measure_predict_latency(
    predict: Callable[..., float],
    *,
    text: str,
    repeats: int,
    warmup: int,
    is_final: bool,
) -> dict[str, Any]:
    if repeats <= 0:
        raise ValueError("repeats must be positive")
    if warmup < 0:
        raise ValueError("warmup must be non-negative")

    last_prediction = 0.0
    for _ in range(warmup):
        last_prediction = float(predict(text, is_final=is_final))

    durations_ms: list[float] = []
    for _ in range(repeats):
        started_ns = time.perf_counter_ns()
        last_prediction = float(predict(text, is_final=is_final))
        elapsed_ns = time.perf_counter_ns() - started_ns
        durations_ms.append(elapsed_ns / 1_000_000.0)

    return {
        "text": text,
        "is_final": is_final,
        "repeats": repeats,
        "warmup": warmup,
        "last_prediction": last_prediction,
        "mean_ms": statistics.fmean(durations_ms),
        "p50_ms": percentile(durations_ms, 50.0),
        "p95_ms": percentile(durations_ms, 95.0),
        "min_ms": min(durations_ms),
        "max_ms": max(durations_ms),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark hot semantic saturation predict latency after one model load.",
    )
    parser.add_argument("text")
    parser.add_argument(
        "--model",
        default=Path("make-model/artifacts/saturation-model.json"),
        type=Path,
    )
    parser.add_argument("--repeats", default=1000, type=int)
    parser.add_argument("--warmup", default=100, type=int)
    parser.add_argument("--final", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    load_started_ns = time.perf_counter_ns()
    model = HashRidgeSaturationModel.load(args.model)
    load_ms = (time.perf_counter_ns() - load_started_ns) / 1_000_000.0
    result = measure_predict_latency(
        model.predict,
        text=args.text,
        repeats=args.repeats,
        warmup=args.warmup,
        is_final=args.final,
    )
    result["model"] = str(args.model)
    result["load_ms"] = load_ms

    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return

    print(f"MODEL_LOAD_MS={result['load_ms']:.4f}")
    print(f"LAST_SATURATION={result['last_prediction']:.4f}")
    print(f"REPEATS={result['repeats']}")
    print(f"WARMUP={result['warmup']}")
    print(f"MEAN_MS={result['mean_ms']:.6f}")
    print(f"P50_MS={result['p50_ms']:.6f}")
    print(f"P95_MS={result['p95_ms']:.6f}")
    print(f"MIN_MS={result['min_ms']:.6f}")
    print(f"MAX_MS={result['max_ms']:.6f}")


if __name__ == "__main__":
    main()
