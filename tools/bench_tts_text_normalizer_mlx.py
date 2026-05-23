from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.normalize_tts_text_mlx import (  # noqa: E402
    DEFAULT_MODEL,
    DEFAULT_RUNNER,
    _load_model,
    normalize_with_loaded_model,
)

SAMPLES = [
    "うん、わかった。少し待ってね。",
    "トモコ、today の meeting は 3pm からだから、schedule を確認して。",
    "I think TAXONOMY の話じゃなくて、今日は grocery list を見たい。",
    "明日の 10:30am に Zoom call があるから remind して。",
    "この API response、たぶん timeout してる。retry した方がいい？",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--runner", choices=["mlx_vlm", "mlx_lm"], default=DEFAULT_RUNNER)
    parser.add_argument("--output-dir", default="logs/tts-text-normalizer")
    parser.add_argument("--max-tokens", type=int, default=80)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    load_start = time.perf_counter()
    model, tokenizer = _load_model(args.model, args.runner)
    load_ms = (time.perf_counter() - load_start) * 1000

    rows = []
    for sample in SAMPLES:
        result = normalize_with_loaded_model(
            model=model,
            tokenizer=tokenizer,
            model_name=args.model,
            runner=args.runner,
            text=sample,
            load_ms=0.0,
            max_tokens=args.max_tokens,
        )
        rows.append(result)

    jsonl_path = output_dir / "results.jsonl"
    with jsonl_path.open("w") as f:
        for row in rows:
            f.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")

    md_path = output_dir / "summary.md"
    with md_path.open("w") as f:
        f.write("# TTS Text Normalizer Bench\n\n")
        f.write(f"- model: `{args.model}`\n")
        f.write(f"- runner: `{args.runner}`\n")
        f.write(f"- load_ms: `{load_ms:.1f}`\n\n")
        f.write("| input | output | first_token_ms | first_text_ms | total_ms | tokens | tok/s |\n")
        f.write("|---|---|---:|---:|---:|---:|---:|\n")
        for row in rows:
            f.write(
                "| "
                f"{_escape(row.input)} | {_escape(row.output)} | "
                f"{row.first_token_ms:.1f} | {row.first_text_ms:.1f} | "
                f"{row.total_ms:.1f} | "
                f"{row.generated_tokens} | {row.tokens_per_sec:.1f} |\n"
            )

    print(md_path.read_text())


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    main()
