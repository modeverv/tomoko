from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_MODEL = "mlx-community/gemma-4-e2b-it-4bit"
DEFAULT_RUNNER = "mlx_vlm"

SYSTEM_PROMPT = """あなたは音声読み上げ用の日本語正規化器です。
入力文を、TTS が自然に読める日本語の一文へ変換してください。

規則:
- 出力は変換後の本文だけ。説明、引用符、箇条書き、前置きは禁止。
- 意味は変えない。
- 英語、略語、時刻、日付、数字、単位は自然な日本語表記へ直す。
- 英字は原則として残さない。一般語は日本語訳またはカタカナにする。
- すでに自然な日本語ならほぼそのまま返す。
- 絵文字、装飾記号、Markdown は出さない。
"""


@dataclass(slots=True)
class NormalizeResult:
    input: str
    output: str
    model: str
    runner: str
    load_ms: float
    first_token_ms: float
    first_text_ms: float
    total_ms: float
    generated_tokens: int
    tokens_per_sec: float


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--runner", choices=["mlx_vlm", "mlx_lm"], default=DEFAULT_RUNNER)
    parser.add_argument("--text", required=True)
    parser.add_argument("--max-tokens", type=int, default=80)
    parser.add_argument("--output-json", default=None)
    args = parser.parse_args()

    result = normalize_once(
        model_name=args.model,
        runner=args.runner,
        text=args.text,
        max_tokens=args.max_tokens,
    )
    print(result.output)
    print(json.dumps(asdict(result), ensure_ascii=False), file=sys.stderr)

    if args.output_json:
        output_path = Path(args.output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n",
        )


def normalize_once(
    *,
    model_name: str,
    runner: str = DEFAULT_RUNNER,
    text: str,
    max_tokens: int = 80,
) -> NormalizeResult:
    load_start = time.perf_counter()
    model, tokenizer = _load_model(model_name, runner)
    load_ms = (time.perf_counter() - load_start) * 1000
    return normalize_with_loaded_model(
        model=model,
        tokenizer=tokenizer,
        model_name=model_name,
        runner=runner,
        text=text,
        load_ms=load_ms,
        max_tokens=max_tokens,
    )


def normalize_with_loaded_model(
    *,
    model: Any,
    tokenizer: Any,
    model_name: str,
    runner: str = DEFAULT_RUNNER,
    text: str,
    load_ms: float = 0.0,
    max_tokens: int = 80,
) -> NormalizeResult:
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
            first_token_ms = (time.perf_counter() - start) * 1000
        if first_text_ms is None and response.text:
            first_text_ms = (time.perf_counter() - start) * 1000
        output_parts.append(response.text)
        generated_tokens = response.generation_tokens
        tokens_per_sec = response.generation_tps

    total_ms = (time.perf_counter() - start) * 1000
    output = _clean_model_output("".join(output_parts))
    return NormalizeResult(
        input=text,
        output=output,
        model=model_name,
        runner=runner,
        load_ms=load_ms,
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
    return f"{SYSTEM_PROMPT}\n\n入力:\n{text}\n\n出力:\n"


def _clean_model_output(output: str) -> str:
    cleaned = output.strip()
    cleaned = re.sub(r"^```(?:text|日本語)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    cleaned = re.sub(r"^(出力|変換後|読み上げ用)[:：]\s*", "", cleaned).strip()
    if "\n" in cleaned:
        cleaned = cleaned.splitlines()[0].strip()
    return cleaned.strip("「」\"'")


if __name__ == "__main__":
    main()
