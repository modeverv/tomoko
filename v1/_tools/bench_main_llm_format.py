#!/usr/bin/env python3
# ruff: noqa: E402
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.gateway.thinking.fast import EMOTIONS, ThinkFastMode
from server.shared.inference.backends.lm_studio import LMStudioBackend
from server.shared.models import ThinkingInput, Transcript

SAMPLE_TURNS = [
    "トモコ、メイン推論モデルを変えるか迷ってる。速度と会話の自然さ、どっちを優先するべき？",
    "出力は必ずEMOTION行から始めたい。モデルがthinkingや英語を混ぜたら音声側が困る。",
    "前の会話も少し覚えてる前提で、短く相談に乗って。26B、E4B、LFMを比較してる。",
    "ここまでを踏まえて、本番のTomokoに入れるならどう判断する？",
]

RECENT_TURNS = [
    Transcript(
        text="トモコ、最近は返答の初速をかなり気にしてる。",
        speaker="seijiro",
        device_id="bench-main-llm-format",
        audio_level_db=-20.0,
        recorded_at=datetime.now(UTC),
        is_final=True,
    ),
    Transcript(
        text="うん、体感で待たされると会話のリズムが崩れるもんね。",
        speaker="tomoko",
        device_id="bench-main-llm-format",
        audio_level_db=-20.0,
        recorded_at=datetime.now(UTC),
        is_final=True,
    ),
]


@dataclass(frozen=True)
class BenchModel:
    label: str
    model: str
    chat_template_kwargs: dict[str, Any] | None = None


def _default_models() -> list[BenchModel]:
    return [
        BenchModel(
            label="lfm25_8b_a1b_mlx_4bit",
            model="lfm2.5-8b-a1b-mlx",
        ),
        BenchModel(
            label="gemma4_e4b_4bit",
            model="gemma-4-e4b-it-mlx",
            chat_template_kwargs={"enable_thinking": False},
        ),
        BenchModel(
            label="gemma4_26b_a4b_4bit",
            model="gemma-4-26b-a4b-it-mlx",
            chat_template_kwargs={"enable_thinking": False},
        ),
    ]


async def _run_model(
    *,
    model: BenchModel,
    base_url: str,
    max_tokens: int,
    repeat: int,
) -> dict[str, Any]:
    backend = LMStudioBackend(
        name=model.label,
        url=base_url,
        model=model.model,
        max_tokens=max_tokens,
        timeout_sec=120.0,
        chat_template_kwargs=model.chat_template_kwargs,
    )
    thinking_mode = ThinkFastMode(prompt_log_path=None)
    results: list[dict[str, Any]] = []

    for iteration in range(1, repeat + 1):
        for turn_index, user_text in enumerate(SAMPLE_TURNS, start=1):
            thinking_input = ThinkingInput(
                text=user_text,
                speaker="seijiro",
                context=RECENT_TURNS,
                emotion="neutral",
                device_id="bench-main-llm-format",
            )
            prepared = thinking_mode.prepare_prompt(thinking_input)
            started = time.perf_counter()
            first_delta_ms: float | None = None
            chunks: list[str] = []
            error: str | None = None
            try:
                async for chunk in backend.chat_stream(
                    prepared.system_prompt,
                    prepared.messages,
                    max_tokens=max_tokens,
                    trace_role=f"main_llm_format:{model.label}",
                ):
                    if first_delta_ms is None:
                        first_delta_ms = _elapsed_ms(started)
                    chunks.append(chunk)
            except Exception as exc:  # noqa: BLE001 - benchmark artifact should capture failures
                error = f"{type(exc).__name__}: {exc}"
            total_ms = _elapsed_ms(started)
            raw_text = "".join(chunks).strip()
            parsed = _inspect_output(raw_text)
            results.append(
                {
                    "iteration": iteration,
                    "turn": turn_index,
                    "user": user_text,
                    "first_delta_ms": first_delta_ms,
                    "total_ms": total_ms,
                    "error": error,
                    "raw_text": raw_text,
                    **parsed,
                }
            )

    successful = [item for item in results if item["error"] is None]
    first_values = [
        item["first_delta_ms"]
        for item in successful
        if item["first_delta_ms"] is not None
    ]
    total_values = [item["total_ms"] for item in successful]
    return {
        "label": model.label,
        "model": model.model,
        "chat_template_kwargs": model.chat_template_kwargs,
        "summary": _summary(successful, first_values, total_values),
        "turns": results,
    }


def _inspect_output(raw_text: str) -> dict[str, Any]:
    stripped = raw_text.lstrip()
    first_line = stripped.splitlines()[0].strip() if stripped else ""
    emotion: str | None = None
    starts_with_emotion = first_line.startswith("EMOTION:")
    valid_emotion = False
    if starts_with_emotion:
        emotion = first_line.removeprefix("EMOTION:").strip()
        valid_emotion = emotion in EMOTIONS

    body = "\n".join(stripped.splitlines()[1:]).strip() if starts_with_emotion else stripped
    thinking_leak = _has_thinking_leak(stripped)
    ascii_alpha_count = len(re.findall(r"[A-Za-z]", body))
    return {
        "first_line": first_line,
        "starts_with_emotion": starts_with_emotion,
        "emotion": emotion,
        "valid_emotion": valid_emotion,
        "thinking_leak": thinking_leak,
        "ascii_alpha_count": ascii_alpha_count,
        "body": body,
        "body_char_count": len(body),
        "basic_format_ok": (
            starts_with_emotion
            and valid_emotion
            and not thinking_leak
            and ascii_alpha_count == 0
            and bool(body)
        ),
    }


def _has_thinking_leak(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "<think",
        "</think",
        "chain of thought",
        "we need answer",
        "need answer",
        "reasoning",
        "thought",
        "<|im_start|>",
        "<|channel>",
    )
    return any(marker in lowered for marker in markers)


def _summary(
    successful: list[dict[str, Any]],
    first_values: list[float],
    total_values: list[float],
) -> dict[str, Any]:
    count = len(successful)
    format_ok = sum(1 for item in successful if item["basic_format_ok"])
    return {
        "success_count": count,
        "format_ok_count": format_ok,
        "format_ok_rate": format_ok / count if count else 0.0,
        "emotion_start_count": sum(
            1 for item in successful if item["starts_with_emotion"]
        ),
        "valid_emotion_count": sum(1 for item in successful if item["valid_emotion"]),
        "thinking_leak_count": sum(1 for item in successful if item["thinking_leak"]),
        "ascii_body_count": sum(
            1 for item in successful if item["ascii_alpha_count"] > 0
        ),
        "avg_first_delta_ms": _avg(first_values),
        "min_first_delta_ms": min(first_values) if first_values else None,
        "max_first_delta_ms": max(first_values) if first_values else None,
        "avg_total_ms": _avg(total_values),
        "min_total_ms": min(total_values) if total_values else None,
        "max_total_ms": max(total_values) if total_values else None,
    }


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    results = []
    for model in _default_models():
        results.append(
            await _run_model(
                model=model,
                base_url=args.base_url,
                max_tokens=args.max_tokens,
                repeat=args.repeat,
            )
        )
    return {
        "measured_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "base_url": args.base_url,
        "max_tokens": args.max_tokens,
        "repeat": args.repeat,
        "models": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:1234")
    parser.add_argument("--max-tokens", type=int, default=96)
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    result = asyncio.run(_run(args))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summaries = [m["summary"] | {"label": m["label"]} for m in result["models"]]
    print(json.dumps(summaries, ensure_ascii=False))


if __name__ == "__main__":
    main()
