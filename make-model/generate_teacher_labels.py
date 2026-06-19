#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import random
from pathlib import Path
from typing import Any

from make_model.schema import PrefixExample, read_jsonl, write_jsonl
from make_model.teacher import (
    DEFAULT_TEACHER_MODEL,
    DEFAULT_TEACHER_URL,
    OpenAICompatibleTeacher,
    TeacherBackend,
    TeacherConfig,
    label_prefix_examples,
)


class DeterministicOnlyTeacher:
    async def complete(self, prompt: str) -> str:
        raise RuntimeError("deterministic-only teacher smoke")


def select_prefix_rows(
    rows: list[dict[str, Any]],
    *,
    limit: int | None = None,
    sample_size: int | None = None,
    sample_seed: int = 0,
) -> list[dict[str, Any]]:
    if limit is not None and sample_size is not None:
        raise ValueError("--limit and --sample-size cannot be used together")
    if limit is not None:
        if limit < 0:
            raise ValueError("--limit must be non-negative")
        return rows[:limit]
    if sample_size is not None:
        if sample_size < 0:
            raise ValueError("--sample-size must be non-negative")
        if sample_size >= len(rows):
            return list(rows)
        rng = random.Random(sample_seed)
        selected_indexes = sorted(rng.sample(range(len(rows)), sample_size))
        return [rows[index] for index in selected_indexes]
    return rows


async def _run(args: argparse.Namespace) -> None:
    rows = read_jsonl(args.prefixes)
    rows = select_prefix_rows(
        rows,
        limit=args.limit,
        sample_size=args.sample_size,
        sample_seed=args.sample_seed,
    )
    examples = [PrefixExample.from_json(row) for row in rows]
    teacher: TeacherBackend
    if args.deterministic_only:
        teacher = DeterministicOnlyTeacher()
    else:
        teacher = OpenAICompatibleTeacher(
            url=args.url,
            model=args.model,
            max_tokens=args.max_tokens,
            timeout_sec=args.timeout_sec,
        )
    labels = await label_prefix_examples(
        examples,
        teacher=teacher,
        config=TeacherConfig(
            source_model=args.model,
            fallback_on_error=not args.no_fallback,
            sleep_sec=args.sleep_sec,
        ),
    )
    write_jsonl(args.out, (label.to_json() for label in labels))
    print(f"wrote {len(labels)} teacher labels to {args.out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate saturation teacher labels.")
    parser.add_argument("--prefixes", default=Path("make-model/data/prefixes.jsonl"), type=Path)
    parser.add_argument("--out", default=Path("make-model/data/teacher-labels.jsonl"), type=Path)
    parser.add_argument("--url", default=DEFAULT_TEACHER_URL)
    parser.add_argument("--model", default=DEFAULT_TEACHER_MODEL)
    parser.add_argument("--max-tokens", default=16, type=int)
    parser.add_argument("--timeout-sec", default=60.0, type=float)
    parser.add_argument("--sleep-sec", default=0.0, type=float)
    parser.add_argument("--limit", default=None, type=int)
    parser.add_argument("--sample-size", default=None, type=int)
    parser.add_argument("--sample-seed", default=20260619, type=int)
    parser.add_argument("--no-fallback", action="store_true")
    parser.add_argument("--deterministic-only", action="store_true")
    args = parser.parse_args()
    if args.limit is not None and args.sample_size is not None:
        parser.error("--limit and --sample-size cannot be used together")

    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
