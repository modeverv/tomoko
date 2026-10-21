#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from make_model.japanese_daily_dialogue import (
    JDD_REPO_URL,
    convert_japanese_daily_dialogue,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download Japanese Daily Dialogue and convert it for teacher labeling."
    )
    parser.add_argument(
        "--source-dir",
        default=Path("make-model/data/external/japanese-daily-dialogue"),
        type=Path,
    )
    parser.add_argument("--repo-url", default=JDD_REPO_URL)
    parser.add_argument("--no-download", action="store_true")
    parser.add_argument(
        "--corpus-out",
        default=Path("make-model/data/japanese-daily-dialogue/corpus.jsonl"),
        type=Path,
    )
    parser.add_argument(
        "--prefixes-out",
        default=Path("make-model/data/japanese-daily-dialogue/prefixes.jsonl"),
        type=Path,
    )
    parser.add_argument(
        "--manifest-out",
        default=Path("make-model/data/japanese-daily-dialogue/manifest.json"),
        type=Path,
    )
    parser.add_argument("--min-chars", default=1, type=int)
    parser.add_argument("--stride-chars", default=1, type=int)
    parser.add_argument("--max-prefixes-per-utterance", default=None, type=int)
    args = parser.parse_args()

    if not args.no_download:
        _ensure_repo(args.source_dir, args.repo_url)

    summary = convert_japanese_daily_dialogue(
        args.source_dir,
        corpus_out=args.corpus_out,
        prefixes_out=args.prefixes_out,
        manifest_out=args.manifest_out,
        min_chars=args.min_chars,
        stride_chars=args.stride_chars,
        max_prefixes_per_utterance=args.max_prefixes_per_utterance,
    )
    print(f"source: {summary.source_dir}")
    print(f"utterances: {summary.utterance_count}")
    print(f"prefixes: {summary.prefix_count}")
    print(f"corpus: {summary.corpus_out}")
    print(f"prefixes_jsonl: {summary.prefixes_out}")
    print(f"manifest: {summary.manifest_out}")


def _ensure_repo(source_dir: Path, repo_url: str) -> None:
    if (source_dir / ".git").exists():
        subprocess.run(
            ["git", "-C", str(source_dir), "pull", "--ff-only"],
            check=True,
        )
        return
    source_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth", "1", repo_url, str(source_dir)],
        check=True,
    )


if __name__ == "__main__":
    main()
