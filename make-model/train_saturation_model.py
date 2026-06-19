#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from make_model.schema import TeacherLabel, read_jsonl
from make_model.training import TrainConfig, train_hash_ridge_model


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a distilled saturation scorer.")
    parser.add_argument("--labels", default=Path("make-model/data/teacher-labels.jsonl"), type=Path)
    parser.add_argument(
        "--out",
        default=Path("make-model/artifacts/saturation-model.json"),
        type=Path,
    )
    parser.add_argument(
        "--metrics-out",
        default=Path("make-model/artifacts/train-metrics.json"),
        type=Path,
    )
    parser.add_argument("--hash-size", default=2048, type=int)
    parser.add_argument("--ngram-min", default=1, type=int)
    parser.add_argument("--ngram-max", default=4, type=int)
    parser.add_argument("--ridge-lambda", default=1.0, type=float)
    args = parser.parse_args()

    labels = [TeacherLabel.from_json(row) for row in read_jsonl(args.labels)]
    _, metrics = train_hash_ridge_model(
        labels,
        TrainConfig(
            hash_size=args.hash_size,
            ngram_min=args.ngram_min,
            ngram_max=args.ngram_max,
            ridge_lambda=args.ridge_lambda,
        ),
        artifact_path=args.out,
    )
    args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_out.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"wrote model to {args.out}")
    print(f"wrote metrics to {args.metrics_out}")


if __name__ == "__main__":
    main()
