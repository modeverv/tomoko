#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from make_model.append_dedupe import (
    AppendDedupeConfig,
    AppendDedupeExample,
    evaluate_append_dedupe_model,
    fit_append_dedupe_model,
)
from make_model.schema import read_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a distilled append dedupe scorer.")
    parser.add_argument(
        "--labels",
        default=Path("make-model/data/public-synthetic/append-dedupe-labels.jsonl"),
        type=Path,
    )
    parser.add_argument(
        "--out",
        default=Path("make-model/artifacts/public-synthetic-append-dedupe-model.json"),
        type=Path,
    )
    parser.add_argument(
        "--metrics-out",
        default=Path("make-model/artifacts/public-synthetic-append-dedupe-train-metrics.json"),
        type=Path,
    )
    parser.add_argument("--hash-size", default=2048, type=int)
    parser.add_argument("--ngram-min", default=1, type=int)
    parser.add_argument("--ngram-max", default=4, type=int)
    parser.add_argument("--ridge-lambda", default=0.05, type=float)
    parser.add_argument("--heuristic-weight", default=0.35, type=float)
    args = parser.parse_args()

    examples = [AppendDedupeExample.from_json(row) for row in read_jsonl(args.labels)]
    model = fit_append_dedupe_model(
        examples,
        AppendDedupeConfig(
            hash_size=args.hash_size,
            ngram_min=args.ngram_min,
            ngram_max=args.ngram_max,
            ridge_lambda=args.ridge_lambda,
            heuristic_weight=args.heuristic_weight,
        ),
        metadata={
            "source": "public_synthetic_append_dedupe",
            "labels_path": str(args.labels),
            "shadow_only": True,
        },
    )
    metrics = evaluate_append_dedupe_model(model, examples)
    model.metadata["train_metrics"] = metrics
    model.save(args.out)
    args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_out.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"wrote model to {args.out}")
    print(f"wrote metrics to {args.metrics_out}")


if __name__ == "__main__":
    main()
