#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from make_model.append_dedupe import (
    AppendDedupeExample,
    HashRidgeAppendDedupeModel,
    evaluate_append_dedupe_model,
)
from make_model.schema import read_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a distilled append dedupe scorer.")
    parser.add_argument(
        "--model",
        default=Path("make-model/artifacts/public-synthetic-append-dedupe-model.json"),
        type=Path,
    )
    parser.add_argument(
        "--labels",
        default=Path("make-model/data/public-synthetic/append-dedupe-labels.jsonl"),
        type=Path,
    )
    args = parser.parse_args()

    model = HashRidgeAppendDedupeModel.load(args.model)
    examples = [AppendDedupeExample.from_json(row) for row in read_jsonl(args.labels)]
    print(
        json.dumps(
            evaluate_append_dedupe_model(model, examples),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
