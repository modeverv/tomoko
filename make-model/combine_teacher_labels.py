#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from make_model.schema import read_jsonl, write_jsonl


def combine_label_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str, str]] = set()
    for path in paths:
        for row in read_jsonl(path):
            key = (
                str(row.get("utterance_id", "")),
                int(row.get("prefix_index", 0)),
                str(row.get("prefix_text", "")),
                str(row.get("label_source", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Combine teacher label JSONL files.")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("labels", nargs="+", type=Path)
    args = parser.parse_args()

    rows = combine_label_rows(args.labels)
    write_jsonl(args.out, rows)
    print(f"combined {len(rows)} labels into {args.out}")


if __name__ == "__main__":
    main()
