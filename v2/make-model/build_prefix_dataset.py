#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from make_model.corpus import build_prefix_examples, load_corpus
from make_model.schema import write_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(description="Build partial-prefix examples from a corpus.")
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--out", default=Path("make-model/data/prefixes.jsonl"), type=Path)
    parser.add_argument("--min-chars", default=1, type=int)
    parser.add_argument("--stride-chars", default=1, type=int)
    parser.add_argument("--max-prefixes-per-utterance", default=None, type=int)
    parser.add_argument("--no-final", action="store_true")
    args = parser.parse_args()

    utterances = load_corpus(args.corpus)
    examples = build_prefix_examples(
        utterances,
        min_chars=args.min_chars,
        stride_chars=args.stride_chars,
        include_final=not args.no_final,
        max_prefixes_per_utterance=args.max_prefixes_per_utterance,
    )
    write_jsonl(args.out, (example.to_json() for example in examples))
    print(f"wrote {len(examples)} prefix examples to {args.out}")


if __name__ == "__main__":
    main()
