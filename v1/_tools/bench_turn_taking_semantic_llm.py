#!/usr/bin/env python3
# ruff: noqa: E402
from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.shared.inference.backends.gemma_mlx import GemmaMLXBackend
from server.shared.inference.backends.mlx_lm import MLXLMBackend


@dataclass(frozen=True)
class SemanticCase:
    case_id: str
    text: str
    expected_saturation: float
    expected_remaining_risk: float
    expected_finished: bool
    category: str


@dataclass(frozen=True)
class ModelTarget:
    label: str
    backend_type: str
    model: str


DEFAULT_CASES = [
    SemanticCase("complete_1", "今日はもう寝るね。", 0.95, 0.05, True, "complete"),
    SemanticCase("complete_2", "それで大丈夫です。", 0.90, 0.10, True, "complete"),
    SemanticCase("complete_3", "今の説明でわかった。", 0.90, 0.10, True, "complete"),
    SemanticCase("complete_4", "トモコ、短く返事して。", 0.95, 0.05, True, "complete"),
    SemanticCase("complete_5", "明日の予定を確認して。", 0.95, 0.05, True, "complete"),
    SemanticCase("complete_6", "うん、そうだね。", 0.85, 0.15, True, "complete"),
    SemanticCase("fragment_1", "えっと、明日の会議なんだけど", 0.25, 0.75, False, "fragment"),
    SemanticCase("fragment_2", "それでさ", 0.15, 0.85, False, "fragment"),
    SemanticCase("fragment_3", "昨日話してたモデルの件、", 0.25, 0.75, False, "fragment"),
    SemanticCase("fragment_4", "もし今の速度を優先するなら", 0.35, 0.65, False, "fragment"),
    SemanticCase("fragment_5", "あともう一つ", 0.20, 0.80, False, "fragment"),
    SemanticCase("fragment_6", "速度は大事だけど、品質も", 0.45, 0.55, False, "fragment"),
    SemanticCase("ambiguous_1", "たぶんそれで", 0.55, 0.45, False, "ambiguous"),
    SemanticCase("ambiguous_2", "まあ、いいかな", 0.70, 0.30, True, "ambiguous"),
    SemanticCase("ambiguous_3", "それってつまり", 0.35, 0.65, False, "ambiguous"),
    SemanticCase("ambiguous_4", "あー、そういうことか", 0.80, 0.20, True, "ambiguous"),
]


def default_targets() -> list[ModelTarget]:
    return [
        ModelTarget(
            label="lfm2_350m_extract",
            backend_type="mlx_lm",
            model="LiquidAI/LFM2-350M-Extract",
        ),
        ModelTarget(
            label="gemma4_e2b_mlx",
            backend_type="gemma_mlx",
            model="mlx-community/gemma-4-e2b-it-4bit",
        ),
    ]


def turn_taking_schema() -> dict[str, Any]:
    return {
        "name": "turn_taking_v2_advisory",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "semantic_saturation": {
                    "type": "number",
                    "description": "0.0から1.0。発話が文末として意味的に完了しているほど高い。",
                },
                "remaining_info_risk": {
                    "type": "number",
                    "description": "0.0から1.0。まだ後続情報が続きそうなほど高い。",
                },
            },
            "required": ["semantic_saturation", "remaining_info_risk"],
            "additionalProperties": False,
        },
    }


def semantic_system_prompt() -> str:
    return (
        "あなたは音声対話システムの発話途中判定器です。"
        "ユーザーの日本語発話断片を読み、今すぐ返答してよいほど意味が完了しているかを判定します。"
        "必ずJSONオブジェクトだけを返してください。説明、Markdown、コードブロックは禁止です。"
    )


def semantic_user_prompt(text: str) -> str:
    return "\n".join(
        [
            "次の日本語発話を分析してください。",
            "",
            f"発話: {json.dumps(text, ensure_ascii=False)}",
            "",
            "判定基準:",
            "- semantic_saturation は、発話が意味的に完了し返答開始してよいほど高くする。",
            "- remaining_info_risk は、まだ後続の語句や説明が続きそうなほど高くする。",
            "- 「それでさ」「もし〜なら」「〜なんだけど」のような接続・前置きは低 saturation。",
            "- 依頼、質問、完了した短い相槌は高 saturation。",
        ]
    )


def compact_system_prompt() -> str:
    return (
        "You are a JSON extraction engine for Japanese speech turn-taking. "
        'Return only one JSON object like {"semantic_saturation": 0.0, '
        '"remaining_info_risk": 1.0}. Do not copy a schema. Do not include '
        "Markdown or explanations. Values must be numbers between 0 and 1."
    )


def compact_user_prompt(text: str) -> str:
    return "\n".join(
        [
            "日本語発話の完了度を数値化してください。",
            f"発話: {json.dumps(text, ensure_ascii=False)}",
            "",
            "semantic_saturation: 発話が文末として意味的に完了しているほど高い。",
            "remaining_info_risk: まだ後続の語句や説明が続きそうなほど高い。",
            "出力は値入りJSONだけ。schemaや説明は返さない。",
        ]
    )


async def run_target(
    target: ModelTarget,
    *,
    cases: list[SemanticCase],
    repeat: int,
    max_tokens: int,
    prompt_style: str,
) -> dict[str, Any]:
    backend = make_backend(target, max_tokens=max_tokens)
    rows: list[dict[str, Any]] = []
    for iteration in range(1, repeat + 1):
        for case in cases:
            started = time.perf_counter()
            first_delta_ms: float | None = None
            chunks: list[str] = []
            error: str | None = None
            try:
                stream = build_stream(
                    backend,
                    case=case,
                    target=target,
                    max_tokens=max_tokens,
                    prompt_style=prompt_style,
                )
                async for chunk in stream:
                    if first_delta_ms is None:
                        first_delta_ms = elapsed_ms(started)
                    chunks.append(chunk)
            except Exception as exc:  # noqa: BLE001 - benchmark artifact should preserve failures
                error = f"{type(exc).__name__}: {exc}"
            total_ms = elapsed_ms(started)
            raw = "".join(chunks).strip()
            parsed = parse_semantic_json(raw)
            rows.append(
                {
                    "iteration": iteration,
                    "target": target.label,
                    "model": target.model,
                    "prompt_style": prompt_style,
                    "case": asdict(case),
                    "first_delta_ms": first_delta_ms,
                    "total_ms": total_ms,
                    "error": error,
                    "raw_response": raw,
                    **parsed,
                    **score_case(case, parsed),
                }
            )
    return {
        "label": target.label,
        "backend_type": target.backend_type,
        "model": target.model,
        "prompt_style": prompt_style,
        "summary": summarize(rows),
        "rows": rows,
    }


def build_stream(
    backend: Any,
    *,
    case: SemanticCase,
    target: ModelTarget,
    max_tokens: int,
    prompt_style: str,
) -> Any:
    trace_role = f"turn_taking_semantic_bench:{target.label}:{prompt_style}"
    if prompt_style == "structured":
        return backend.chat_stream_structured(
            semantic_system_prompt(),
            [{"role": "user", "content": semantic_user_prompt(case.text)}],
            json_schema=turn_taking_schema(),
            max_tokens=max_tokens,
            trace_role=trace_role,
        )
    if prompt_style == "compact":
        return backend.chat_stream(
            compact_system_prompt(),
            [{"role": "user", "content": compact_user_prompt(case.text)}],
            trace_role=trace_role,
        )
    raise ValueError(f"unknown prompt_style: {prompt_style}")


def make_backend(target: ModelTarget, *, max_tokens: int):
    if target.backend_type == "mlx_lm":
        return MLXLMBackend(name=target.label, model=target.model, max_tokens=max_tokens)
    if target.backend_type == "gemma_mlx":
        return GemmaMLXBackend(name=target.label, model=target.model, max_tokens=max_tokens)
    raise ValueError(f"unknown backend_type: {target.backend_type}")


def parse_semantic_json(raw: str) -> dict[str, Any]:
    json_text = extract_json_object(raw)
    if json_text is None:
        return {
            "parse_ok": False,
            "parse_error": "json_object_not_found",
            "shape_ok": False,
            "semantic_saturation": None,
            "remaining_info_risk": None,
            "range_ok": False,
        }
    try:
        parsed = json.loads(json_text)
        expected_keys = {"semantic_saturation", "remaining_info_risk"}
        if not isinstance(parsed, dict):
            raise TypeError("parsed JSON is not an object")
        shape_ok = set(parsed) == expected_keys
        semantic_saturation = float(parsed["semantic_saturation"])
        remaining_info_risk = float(parsed["remaining_info_risk"])
    except Exception as exc:  # noqa: BLE001 - parse diagnostics go into artifact
        return {
            "parse_ok": False,
            "parse_error": f"{type(exc).__name__}: {exc}",
            "shape_ok": False,
            "semantic_saturation": None,
            "remaining_info_risk": None,
            "range_ok": False,
        }
    range_ok = (
        math.isfinite(semantic_saturation)
        and math.isfinite(remaining_info_risk)
        and 0.0 <= semantic_saturation <= 1.0
        and 0.0 <= remaining_info_risk <= 1.0
    )
    return {
        "parse_ok": True,
        "parse_error": None,
        "shape_ok": shape_ok,
        "semantic_saturation": semantic_saturation,
        "remaining_info_risk": remaining_info_risk,
        "range_ok": range_ok,
    }


def extract_json_object(raw: str) -> str | None:
    stripped = raw.strip()
    if not stripped:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL)
    if fenced:
        return fenced.group(1)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return stripped[start : end + 1]


def score_case(case: SemanticCase, parsed: dict[str, Any]) -> dict[str, Any]:
    if not parsed["parse_ok"] or not parsed["shape_ok"] or not parsed["range_ok"]:
        return {
            "predicted_finished": None,
            "saturation_abs_error": None,
            "remaining_risk_abs_error": None,
            "finished_correct": False,
        }
    saturation = float(parsed["semantic_saturation"])
    risk = float(parsed["remaining_info_risk"])
    predicted_finished = saturation >= 0.75 and risk <= 0.40
    return {
        "predicted_finished": predicted_finished,
        "saturation_abs_error": abs(saturation - case.expected_saturation),
        "remaining_risk_abs_error": abs(risk - case.expected_remaining_risk),
        "finished_correct": predicted_finished == case.expected_finished,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [row for row in rows if row["error"] is None]
    parsed = [row for row in completed if row["parse_ok"]]
    shaped = [row for row in parsed if row["shape_ok"]]
    ranged = [row for row in shaped if row["range_ok"]]
    saturation_errors = [
        row["saturation_abs_error"]
        for row in ranged
        if row["saturation_abs_error"] is not None
    ]
    remaining_errors = [
        row["remaining_risk_abs_error"]
        for row in ranged
        if row["remaining_risk_abs_error"] is not None
    ]
    first_values = [
        row["first_delta_ms"]
        for row in completed
        if row["first_delta_ms"] is not None
    ]
    total_values = [row["total_ms"] for row in completed]
    return {
        "row_count": len(rows),
        "completed_count": len(completed),
        "parse_ok_count": len(parsed),
        "shape_ok_count": len(shaped),
        "range_ok_count": len(ranged),
        "parse_ok_rate": len(parsed) / len(completed) if completed else 0.0,
        "shape_ok_rate": len(shaped) / len(parsed) if parsed else 0.0,
        "range_ok_rate": len(ranged) / len(shaped) if shaped else 0.0,
        "finished_accuracy": (
            sum(1 for row in ranged if row["finished_correct"]) / len(ranged)
            if ranged
            else 0.0
        ),
        "saturation_mae": avg(saturation_errors),
        "remaining_risk_mae": avg(remaining_errors),
        "avg_first_delta_ms": avg(first_values),
        "p95_first_delta_ms": percentile(first_values, 95),
        "avg_total_ms": avg(total_values),
        "p95_total_ms": percentile(total_values, 95),
        "confusion": confusion(ranged),
    }


def confusion(rows: list[dict[str, Any]]) -> dict[str, int]:
    tp = fp = tn = fn = 0
    for row in rows:
        expected = bool(row["case"]["expected_finished"])
        predicted = bool(row["predicted_finished"])
        if expected and predicted:
            tp += 1
        elif expected and not predicted:
            fn += 1
        elif not expected and predicted:
            fp += 1
        else:
            tn += 1
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn}


def avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = (len(ordered) - 1) * (p / 100.0)
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return ordered[lo]
    weight = idx - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


async def run(args: argparse.Namespace) -> dict[str, Any]:
    targets = default_targets()
    if args.only:
        selected = set(args.only.split(","))
        targets = [target for target in targets if target.label in selected]
    results = []
    for target in targets:
        results.append(
            await run_target(
                target,
                cases=DEFAULT_CASES,
                repeat=args.repeat,
                max_tokens=args.max_tokens,
                prompt_style=args.prompt_style,
            )
        )
    return {
        "measured_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "repeat": args.repeat,
        "max_tokens": args.max_tokens,
        "prompt_style": args.prompt_style,
        "finished_threshold": {"semantic_saturation_min": 0.75, "remaining_info_risk_max": 0.40},
        "case_count": len(DEFAULT_CASES),
        "models": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--max-tokens", type=int, default=96)
    parser.add_argument(
        "--prompt-style",
        choices=["structured", "compact"],
        default="structured",
        help=(
            "structured matches Tomoko chat_stream_structured; compact tests "
            "extract-only JSON prompting."
        ),
    )
    parser.add_argument(
        "--only",
        help="comma-separated target labels, e.g. lfm2_350m_extract,gemma4_e2b_mlx",
    )
    args = parser.parse_args()
    result = asyncio.run(run(args))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summaries = [
        {"label": model["label"], **model["summary"]}
        for model in result["models"]
    ]
    print(json.dumps(summaries, ensure_ascii=False))


if __name__ == "__main__":
    main()
