#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from benchmark_saturation_latency import percentile
from make_model.append_dedupe import (
    AppendDedupeInput,
    AppendDedupeResult,
    HashRidgeAppendDedupeModel,
)


def measure_append_dedupe_latency(
    predict: Callable[[AppendDedupeInput], AppendDedupeResult],
    *,
    sample: AppendDedupeInput,
    repeats: int,
    warmup: int,
) -> dict[str, Any]:
    if repeats <= 0:
        raise ValueError("repeats must be positive")
    if warmup < 0:
        raise ValueError("warmup must be non-negative")

    last_result: AppendDedupeResult | None = None
    for _ in range(warmup):
        last_result = predict(sample)

    durations_ms: list[float] = []
    for _ in range(repeats):
        started_ns = time.perf_counter_ns()
        last_result = predict(sample)
        elapsed_ns = time.perf_counter_ns() - started_ns
        durations_ms.append(elapsed_ns / 1_000_000.0)

    if last_result is None:
        last_result = predict(sample)
    return {
        "previous_user_text": sample.previous_user_text,
        "current_user_text": sample.current_user_text,
        "time_delta_ms": sample.time_delta_ms,
        "tomoko_speaking": sample.tomoko_speaking,
        "speech_queue_active": sample.speech_queue_active,
        "current_is_final": sample.current_is_final,
        "repeats": repeats,
        "warmup": warmup,
        "last_label": last_result.label,
        "last_duplicate_score": last_result.duplicate_score,
        "last_continuation_score": last_result.continuation_score,
        "last_new_intent_score": last_result.new_intent_score,
        "mean_ms": statistics.fmean(durations_ms),
        "p50_ms": percentile(durations_ms, 50.0),
        "p95_ms": percentile(durations_ms, 95.0),
        "min_ms": min(durations_ms),
        "max_ms": max(durations_ms),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark hot append dedupe predict latency after one model load.",
    )
    parser.add_argument("--previous", required=True)
    parser.add_argument("--current", required=True)
    parser.add_argument("--time-delta-ms", default=1000, type=int)
    parser.add_argument("--tomoko-speaking", action="store_true")
    parser.add_argument("--speech-queue-active", action="store_true")
    parser.add_argument("--not-final", action="store_true")
    parser.add_argument(
        "--model",
        default=Path("make-model/artifacts/public-synthetic-append-dedupe-model.json"),
        type=Path,
    )
    parser.add_argument("--repeats", default=1000, type=int)
    parser.add_argument("--warmup", default=100, type=int)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    load_started_ns = time.perf_counter_ns()
    model = HashRidgeAppendDedupeModel.load(args.model)
    load_ms = (time.perf_counter_ns() - load_started_ns) / 1_000_000.0
    sample = AppendDedupeInput(
        previous_user_text=args.previous,
        current_user_text=args.current,
        time_delta_ms=args.time_delta_ms,
        tomoko_speaking=args.tomoko_speaking,
        speech_queue_active=args.speech_queue_active,
        current_is_final=not args.not_final,
    )
    result = measure_append_dedupe_latency(
        model.predict,
        sample=sample,
        repeats=args.repeats,
        warmup=args.warmup,
    )
    result["model"] = str(args.model)
    result["load_ms"] = load_ms

    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return

    print(f"MODEL_LOAD_MS={result['load_ms']:.4f}")
    print(f"LAST_LABEL={result['last_label']}")
    print(f"LAST_DUPLICATE_SCORE={result['last_duplicate_score']:.4f}")
    print(f"LAST_CONTINUATION_SCORE={result['last_continuation_score']:.4f}")
    print(f"LAST_NEW_INTENT_SCORE={result['last_new_intent_score']:.4f}")
    print(f"REPEATS={result['repeats']}")
    print(f"WARMUP={result['warmup']}")
    print(f"MEAN_MS={result['mean_ms']:.6f}")
    print(f"P50_MS={result['p50_ms']:.6f}")
    print(f"P95_MS={result['p95_ms']:.6f}")
    print(f"MIN_MS={result['min_ms']:.6f}")
    print(f"MAX_MS={result['max_ms']:.6f}")


if __name__ == "__main__":
    main()
