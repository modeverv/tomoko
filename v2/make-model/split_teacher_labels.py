#!/usr/bin/env python3
from __future__ import annotations

import argparse
import random
from pathlib import Path
from typing import Any

from make_model.schema import read_jsonl, write_jsonl


def split_rows(
    rows: list[dict[str, Any]],
    *,
    train_size: int | None = None,
    train_ratio: float = 0.8,
    seed: int = 20260619,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not rows:
        raise ValueError("labels must not be empty")
    if train_size is None:
        if not 0.0 < train_ratio < 1.0:
            raise ValueError("--train-ratio must be between 0.0 and 1.0")
        train_size = int(len(rows) * train_ratio)
    if train_size <= 0 or train_size >= len(rows):
        raise ValueError("--train-size must leave at least one train row and one eval row")

    indexes = list(range(len(rows)))
    random.Random(seed).shuffle(indexes)
    train_indexes = set(indexes[:train_size])
    train_rows = [row for index, row in enumerate(rows) if index in train_indexes]
    eval_rows = [row for index, row in enumerate(rows) if index not in train_indexes]
    return train_rows, eval_rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Split teacher labels into train/eval JSONL.")
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--train-out", required=True, type=Path)
    parser.add_argument("--eval-out", required=True, type=Path)
    parser.add_argument("--train-size", default=None, type=int)
    parser.add_argument("--train-ratio", default=0.8, type=float)
    parser.add_argument("--seed", default=20260619, type=int)
    args = parser.parse_args()

    rows = read_jsonl(args.labels)
    train_rows, eval_rows = split_rows(
        rows,
        train_size=args.train_size,
        train_ratio=args.train_ratio,
        seed=args.seed,
    )
    write_jsonl(args.train_out, train_rows)
    write_jsonl(args.eval_out, eval_rows)
    print(f"read {len(rows)} labels from {args.labels}")
    print(f"wrote {len(train_rows)} train labels to {args.train_out}")
    print(f"wrote {len(eval_rows)} eval labels to {args.eval_out}")


if __name__ == "__main__":
    main()
