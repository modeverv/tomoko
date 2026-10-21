#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

from make_model.schema import write_jsonl

FINAL_TEXT_RE = re.compile(r"stt_observation final='True' text='([^']*)'")


def extract_seed_rows(log_path: Path, *, max_delta_lines: int = 80) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    recent: list[tuple[int, str]] = []
    with log_path.open(encoding="utf-8", errors="replace") as file:
        for line_number, line in enumerate(file, start=1):
            match = FINAL_TEXT_RE.search(line)
            if match is None:
                continue
            text = match.group(1).strip()
            if not text:
                continue
            for previous_line, previous_text in reversed(recent):
                if line_number - previous_line > max_delta_lines:
                    continue
                rows.append(
                    {
                        "previous_user_text": previous_text,
                        "current_user_text": text,
                        "line_delta": line_number - previous_line,
                        "source_log": str(log_path),
                        "source_line": line_number,
                        "note": (
                            "private seed candidate; do not mix into public synthetic artifacts"
                        ),
                    }
                )
                break
            recent.append((line_number, text))
            recent = recent[-12:]
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Extract private append dedupe seed candidates from server logs. "
            "This does not label examples and should not be used for public artifacts."
        ),
    )
    parser.add_argument("--log", default=Path("logs/server-debug.log"), type=Path)
    parser.add_argument(
        "--out",
        default=Path("make-model/data/private-log-seeds/append-dedupe-seeds.jsonl"),
        type=Path,
    )
    parser.add_argument("--max-delta-lines", default=80, type=int)
    args = parser.parse_args()

    rows = extract_seed_rows(args.log, max_delta_lines=args.max_delta_lines)
    write_jsonl(args.out, rows)
    print(f"wrote {len(rows)} private seed candidates to {args.out}")


if __name__ == "__main__":
    main()
