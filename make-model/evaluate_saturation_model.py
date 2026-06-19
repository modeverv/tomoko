#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from make_model.model import HashRidgeSaturationModel, evaluate_model
from make_model.schema import TeacherLabel, read_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a distilled saturation scorer.")
    parser.add_argument(
        "--model",
        default=Path("make-model/artifacts/saturation-model.json"),
        type=Path,
    )
    parser.add_argument("--labels", default=Path("make-model/data/teacher-labels.jsonl"), type=Path)
    parser.add_argument("--threshold", default=0.75, type=float)
    args = parser.parse_args()

    model = HashRidgeSaturationModel.load(args.model)
    labels = [TeacherLabel.from_json(row) for row in read_jsonl(args.labels)]
    metrics = evaluate_model(model, labels, threshold=args.threshold)
    print(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
