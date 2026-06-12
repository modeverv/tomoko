from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_MODEL = "mlx-community/gemma-4-e2b-it-4bit"
DEFAULT_RUNNER = "mlx_vlm"

SYSTEM_PROMPT = """あなたは音声会話中の相槌専用モデルです。
出力は日本語の短い反応を1つだけにしてください。
許可される長さは最大8文字です。
例: うん / あー、それね / マジで？ / なるほど / そっか
説明、引用符、句読点の追加、絵文字、英語は禁止です。
"""

SAMPLES = [
    "昨日さ、駅まで歩いてたら急に雨が降ってきてさ",
    "このコード、なんか同じ処理を三回くらい書いてる気がする",
    "さっきの話なんだけど、たぶん前提が違ってた",
    "コーヒー飲んだのに全然眠気が取れないんだよね",
    "これ、思ったより反応が速くてびっくりした",
    "いや、そこでそうなるのはさすがに予想外だった",
    "今日はちょっと集中力が変な感じなんだよね",
    "ログを見ると一箇所だけ妙に遅いんだよ",
    "それで結局、全部やり直すことになったんだよね",
    "たぶん今の返事は短く挟んでくれるだけでいい",
]


@dataclass(slots=True)
class Row:
    input: str
    output: str
    first_token_ms: float
    first_text_ms: float
    total_ms: float
    generated_tokens: int
    tokens_per_sec: float


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--runner", choices=["mlx_vlm", "mlx_lm"], default=DEFAULT_RUNNER)
    parser.add_argument("--max-tokens", type=int, default=8)
    parser.add_argument("--warmup-runs", type=int, default=2)
    parser.add_argument("--output-dir", default="logs/short-reaction-mlx")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    load_start = time.perf_counter()
    model, tokenizer = _load_model(args.model, args.runner)
    load_ms = _elapsed_ms(load_start)

    for idx in range(args.warmup_runs):
        _run_once(
            model=model,
            tokenizer=tokenizer,
            runner=args.runner,
            text=f"ウォームアップ {idx}",
            max_tokens=args.max_tokens,
        )

    rows = [
        _run_once(
            model=model,
            tokenizer=tokenizer,
            runner=args.runner,
            text=sample,
            max_tokens=args.max_tokens,
        )
        for sample in SAMPLES
    ]

    summary = _summary(rows)
    result = {
        "model": args.model,
        "runner": args.runner,
        "max_tokens": args.max_tokens,
        "warmup_runs": args.warmup_runs,
        "load_ms": load_ms,
        "summary": summary,
        "rows": [asdict(row) for row in rows],
    }

    json_path = output_dir / f"short-reaction-{args.runner}.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")

    md_path = output_dir / f"short-reaction-{args.runner}.md"
    md_path.write_text(_render_markdown(result), encoding="utf-8")
    print(md_path.read_text(encoding="utf-8"))


def _run_once(
    *,
    model: Any,
    tokenizer: Any,
    runner: str,
    text: str,
    max_tokens: int,
) -> Row:
    prompt = _build_prompt(tokenizer, text)
    start = time.perf_counter()
    first_token_ms: float | None = None
    first_text_ms: float | None = None
    output_parts: list[str] = []
    generated_tokens = 0
    tokens_per_sec = 0.0

    for response in _stream_generate(
        runner=runner,
        model=model,
        tokenizer=tokenizer,
        prompt=prompt,
        max_tokens=max_tokens,
    ):
        if first_token_ms is None:
            first_token_ms = _elapsed_ms(start)
        piece = getattr(response, "text", "")
        if piece and first_text_ms is None:
            first_text_ms = _elapsed_ms(start)
        output_parts.append(piece)
        generated_tokens = int(getattr(response, "generation_tokens", generated_tokens))
        tokens_per_sec = float(getattr(response, "generation_tps", tokens_per_sec))

    total_ms = _elapsed_ms(start)
    output = _clean_output("".join(output_parts))
    return Row(
        input=text,
        output=output,
        first_token_ms=first_token_ms or total_ms,
        first_text_ms=first_text_ms or total_ms,
        total_ms=total_ms,
        generated_tokens=generated_tokens,
        tokens_per_sec=tokens_per_sec,
    )


def _load_model(model_name: str, runner: str) -> tuple[Any, Any]:
    if runner == "mlx_vlm":
        from mlx_vlm import load

        return load(model_name)
    from mlx_lm import load

    return load(model_name)


def _stream_generate(
    *,
    runner: str,
    model: Any,
    tokenizer: Any,
    prompt: str,
    max_tokens: int,
):
    if runner == "mlx_vlm":
        from mlx_vlm import stream_generate

        yield from stream_generate(
            model,
            tokenizer,
            prompt,
            max_tokens=max_tokens,
            temperature=0.0,
        )
        return

    from mlx_lm import stream_generate

    yield from stream_generate(
        model,
        tokenizer,
        prompt,
        max_tokens=max_tokens,
        temperature=0.0,
    )


def _build_prompt(tokenizer: Any, text: str) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]
    if hasattr(tokenizer, "apply_chat_template"):
        return tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
    return f"{SYSTEM_PROMPT}\n\nuser:\n{text}\nassistant:\n"


def _clean_output(output: str) -> str:
    cleaned = output.strip()
    for prefix in ("assistant:", "出力:", "反応:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
    return cleaned.strip("「」\"' \n")


def _summary(rows: list[Row]) -> dict[str, float]:
    return {
        "first_text_p50_ms": statistics.median(row.first_text_ms for row in rows),
        "first_text_p95_ms": _percentile([row.first_text_ms for row in rows], 0.95),
        "total_p50_ms": statistics.median(row.total_ms for row in rows),
        "total_p95_ms": _percentile([row.total_ms for row in rows], 0.95),
        "under_500_total_count": sum(1 for row in rows if row.total_ms <= 500.0),
        "under_500_first_text_count": sum(1 for row in rows if row.first_text_ms <= 500.0),
    }


def _percentile(values: list[float], q: float) -> float:
    sorted_values = sorted(values)
    index = min(len(sorted_values) - 1, max(0, round((len(sorted_values) - 1) * q)))
    return sorted_values[index]


def _render_markdown(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines = [
        "# Short Reaction MLX Bench",
        "",
        f"- model: `{result['model']}`",
        f"- runner: `{result['runner']}`",
        f"- max_tokens: `{result['max_tokens']}`",
        f"- warmup_runs: `{result['warmup_runs']}`",
        f"- load_ms: `{result['load_ms']:.1f}`",
        "- first_text p50/p95: "
        f"`{summary['first_text_p50_ms']:.1f}` / "
        f"`{summary['first_text_p95_ms']:.1f}` ms",
        f"- total p50/p95: `{summary['total_p50_ms']:.1f}` / `{summary['total_p95_ms']:.1f}` ms",
        f"- <=500ms first_text: `{summary['under_500_first_text_count']}/10`",
        f"- <=500ms total: `{summary['under_500_total_count']}/10`",
        "",
        "| input | output | first_text_ms | total_ms | tokens | tok/s |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in result["rows"]:
        lines.append(
            "| "
            f"{_escape(row['input'])} | {_escape(row['output'])} | "
            f"{row['first_text_ms']:.1f} | {row['total_ms']:.1f} | "
            f"{row['generated_tokens']} | {row['tokens_per_sec']:.1f} |"
        )
    return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _elapsed_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000


if __name__ == "__main__":
    main()
