"""Latency regression suite for Tomoko v2 (PLAN S16 / f.md Step 4).

Replays seed utterances (macOS ``say`` audio) into a running real-runtime
``/ws`` endpoint, measures voice-end -> first-audio latency per run, classifies
partial-origin vs final-origin starts, and writes a JSON + Markdown report.
Exits non-zero when the latency targets are exceeded, so it can gate
regressions without a human listening.

Usage (with `make run` runtime up):
    uv run python -m scripts.v2_latency_suite --count 10 --repeats 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from scripts.v2_scenario_replay import (
    SAMPLE_RATE,
    AssertionResult,
    TimelineRecorder,
    _receiver,
    _stream_samples,
    _wait_for_event,
    pack_float32,
    read_wav_float32,
    synthesize_say_wav,
)

# final origin は「短い一文の要点 + 続きを append」の実測構造コスト
# (VAD close + STT final + LLM一文目 + TTS)を前提にした値。
# 体感の主経路は partial origin(発話中の先行応答)側で担保する。
DEFAULT_TARGETS = {
    "final_origin_p50_ms": 6000.0,
    "final_origin_p95_ms": 9500.0,
    "partial_origin_p50_ms": 800.0,
}


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * q / 100.0
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def select_seeds(lines: list[str], *, count: int, category: str | None) -> list[str]:
    seeds: list[str] = []
    current_category = ""
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            if "---" in stripped and ":" in stripped:
                current_category = (
                    stripped.split("---", 1)[1].split(":", 1)[0].strip()
                )
            continue
        if category is not None and current_category != category:
            continue
        seeds.append(stripped)
        if len(seeds) >= count:
            break
    return seeds


def classify_run(
    timeline: list[dict[str, Any]],
    *,
    voice_end_elapsed_ms: float,
) -> dict[str, Any]:
    first_final_at: float | None = None
    first_order_at: float | None = None
    first_audio_at: float | None = None
    stage_candidates: list[tuple[float, str, dict[str, float]]] = []
    reconciled = False
    false_early = False
    for entry in timeline:
        elapsed = float(entry.get("elapsed_ms", 0.0))
        entry_type = entry.get("type")
        payload = entry.get("payload", {})
        if entry_type == "transcript" and payload.get("is_final") and first_final_at is None:
            first_final_at = elapsed
        if entry_type == "speech_order" and first_order_at is None:
            first_order_at = elapsed
        if entry_type == "binary_audio" and first_audio_at is None:
            first_audio_at = elapsed
        if entry_type == "latency_stage":
            raw_timings = payload.get("stage_timings_ms", {})
            if isinstance(raw_timings, dict):
                stage_timings = {
                    str(key): float(value)
                    for key, value in raw_timings.items()
                    if isinstance(value, (int, float))
                }
                if stage_timings:
                    stage_candidates.append(
                        (elapsed, str(payload.get("origin", "")), stage_timings)
                    )
        if entry_type == "scheduler_decision" and "reconciled" in str(
            payload.get("reason", "")
        ):
            reconciled = True
        reason = str(payload.get("reason", ""))
        if "diverged" in reason or "false early" in reason:
            false_early = True
    partial_origin = (
        first_order_at is not None
        and (first_final_at is None or first_order_at < first_final_at)
    )
    voice_end_to_final = _delta(first_final_at, voice_end_elapsed_ms)
    voice_end_to_order = _delta(first_order_at, voice_end_elapsed_ms)
    first_audio_ms = _delta(first_audio_at, voice_end_elapsed_ms)
    stage_timings_ms = _select_stage_timings(
        stage_candidates,
        origin="partial" if partial_origin else "final",
        first_order_at=first_order_at,
    )
    return {
        "first_audio_ms": first_audio_ms,
        "voice_end_elapsed_ms": voice_end_elapsed_ms,
        "first_final_elapsed_ms": first_final_at,
        "first_order_elapsed_ms": first_order_at,
        "first_audio_elapsed_ms": first_audio_at,
        "voice_end_to_final_ms": voice_end_to_final,
        "voice_end_to_order_ms": voice_end_to_order,
        "order_to_first_audio_ms": _delta(first_audio_at, first_order_at),
        "final_to_first_audio_ms": _delta(first_audio_at, first_final_at),
        "partial_origin": partial_origin,
        "reconciled": reconciled,
        "false_early": false_early,
        "stage_timings_ms": stage_timings_ms,
    }


def evaluate_targets(
    stats: dict[str, Any],
    targets: dict[str, float],
) -> list[AssertionResult]:
    results: list[AssertionResult] = []
    no_audio = int(stats.get("runs_no_audio") or 0)
    results.append(
        AssertionResult(
            name="runs_no_audio==0",
            ok=no_audio == 0,
            detail=f"actual={no_audio}",
        )
    )
    for key, bound in targets.items():
        actual = stats.get(key)
        if actual is None:
            results.append(
                AssertionResult(
                    name=f"{key}<={bound}",
                    ok=True,
                    detail="no samples (skipped)",
                )
            )
            continue
        results.append(
            AssertionResult(
                name=f"{key}<={bound}",
                ok=float(actual) <= float(bound),
                detail=f"actual={float(actual):.1f}ms",
            )
        )
    return results


@dataclass(slots=True)
class RunResult:
    seed: str
    repeat: int
    first_audio_ms: float | None
    partial_origin: bool
    reconciled: bool
    false_early: bool
    final_transcript: str
    timings: dict[str, float | None] = field(default_factory=dict)
    stage_timings_ms: dict[str, float] = field(default_factory=dict)
    timeline: list[dict[str, Any]] = field(default_factory=list)


def _rate(count: int, total: int) -> float | None:
    if total <= 0:
        return None
    return count / total


def _delta(end: float | None, start: float | None) -> float | None:
    if end is None or start is None:
        return None
    return end - start


def _select_stage_timings(
    candidates: list[tuple[float, str, dict[str, float]]],
    *,
    origin: str,
    first_order_at: float | None,
) -> dict[str, float]:
    if not candidates:
        return {}
    matching = [
        timings
        for elapsed, candidate_origin, timings in candidates
        if candidate_origin == origin
        and (first_order_at is None or elapsed <= first_order_at)
    ]
    if matching:
        return matching[-1]
    origin_only = [
        timings for _elapsed, candidate_origin, timings in candidates if candidate_origin == origin
    ]
    if origin_only:
        return origin_only[-1]
    return candidates[-1][2]


async def run_one_utterance(
    *,
    url: str,
    samples: tuple[float, ...],
    trailing_silence_ms: int,
    timeout_sec: float,
    reset_conversation: bool,
) -> tuple[TimelineRecorder, float]:
    from websockets.asyncio.client import connect

    recorder = TimelineRecorder()
    chunk_samples = 128
    async with connect(url, max_size=None) as websocket:
        receive_task = asyncio.create_task(_receiver(websocket, recorder))
        try:
            if reset_conversation:
                await websocket.send(
                    json.dumps(
                        {"type": "latency_control", "command": "reset_conversation"},
                        ensure_ascii=False,
                    )
                )
                reset_ok = await _wait_for_event(
                    recorder,
                    "latency_control_ack",
                    0.0,
                    timeout_sec=5.0,
                )
                if not reset_ok:
                    raise TimeoutError("timed out waiting for latency_control_ack")
            await _stream_samples(
                websocket, samples, chunk_samples, chunk_samples / SAMPLE_RATE
            )
            voice_end = recorder.elapsed_ms()
            silence_chunk = (0.0,) * chunk_samples
            silence_chunks = math.ceil(
                SAMPLE_RATE * trailing_silence_ms / 1000 / chunk_samples
            )
            for _ in range(silence_chunks):
                if recorder.events_after("binary_audio", voice_end):
                    break
                await websocket.send(pack_float32(silence_chunk))
                await asyncio.sleep(chunk_samples / SAMPLE_RATE)
            deadline = asyncio.get_event_loop().time() + timeout_sec
            while asyncio.get_event_loop().time() < deadline:
                if recorder.events_after("prompt_complete", voice_end):
                    break
                if recorder.events_after("binary_audio", voice_end) and (
                    recorder.events_after("audio_complete", voice_end)
                ):
                    break
                await asyncio.sleep(0.05)
        finally:
            await websocket.close()
            receive_task.cancel()
            try:
                await receive_task
            except asyncio.CancelledError:
                pass
    return recorder, voice_end


def summarize(runs: list[RunResult]) -> dict[str, Any]:
    measured = [run for run in runs if run.first_audio_ms is not None]
    partial = [run.first_audio_ms for run in measured if run.partial_origin]
    final = [run.first_audio_ms for run in measured if not run.partial_origin]
    all_values = [run.first_audio_ms for run in measured]
    partial_count = sum(1 for run in runs if run.partial_origin)
    final_count = sum(1 for run in runs if not run.partial_origin)
    reconciled_count = sum(1 for run in runs if run.reconciled)
    false_early_count = sum(1 for run in runs if run.false_early)
    stats = {
        "runs_total": len(runs),
        "runs_measured": len(measured),
        "runs_no_audio": len(runs) - len(measured),
        "partial_origin_count": partial_count,
        "final_origin_count": final_count,
        "reconciled_count": reconciled_count,
        "false_early_count": false_early_count,
        "partial_origin_rate": _rate(partial_count, len(runs)),
        "final_origin_rate": _rate(final_count, len(runs)),
        "reconcile_rate": _rate(reconciled_count, len(runs)),
        "false_early_rate": _rate(false_early_count, len(runs)),
        "all_p50_ms": percentile(all_values, 50),
        "all_p95_ms": percentile(all_values, 95),
        "partial_origin_p50_ms": percentile(partial, 50),
        "partial_origin_p95_ms": percentile(partial, 95),
        "final_origin_p50_ms": percentile(final, 50),
        "final_origin_p95_ms": percentile(final, 95),
    }
    for key in ("stt_ms", "stt_partial_ms", "tomoko_ms", "tts_ms", "total_ms"):
        values = [
            run.stage_timings_ms[key]
            for run in runs
            if key in run.stage_timings_ms
        ]
        label = key.removesuffix("_ms")
        stats[f"stage_{label}_p50_ms"] = percentile(values, 50)
        stats[f"stage_{label}_p95_ms"] = percentile(values, 95)
    return stats


def render_markdown(
    stats: dict[str, Any],
    results: list[AssertionResult],
    runs: list[RunResult],
    *,
    stamp: str,
) -> str:
    def fmt(value: Any) -> str:
        if value is None:
            return "-"
        if isinstance(value, float):
            return f"{value:.1f}"
        return str(value)

    lines = [
        f"# Tomoko v2 latency suite {stamp}",
        "",
        "## Summary",
        "",
        "| metric | value |",
        "|---|---|",
    ]
    for key, value in stats.items():
        lines.append(f"| {key} | {fmt(value)} |")
    lines.extend(["", "## Targets", ""])
    for result in results:
        marker = "PASS" if result.ok else "FAIL"
        lines.append(f"- {marker} {result.name} ({result.detail})")
    lines.extend(
        [
            "",
            "## Runs",
            "",
            "| seed | repeat | first_audio_ms | origin | reconciled | false_early |",
            "|---|---|---|---|---|---|",
        ]
    )
    for run in runs:
        origin = "partial" if run.partial_origin else "final"
        lines.append(
            f"| {run.seed[:24]} | {run.repeat} | {fmt(run.first_audio_ms)} "
            f"| {origin} | {run.reconciled} | {run.false_early} |"
        )
    timing_header = (
        "| seed | repeat | voice_end_to_final_ms | voice_end_to_order_ms | "
        "order_to_first_audio_ms | final_to_first_audio_ms |"
    )
    lines.extend(["", "## Timing Breakdown", "", timing_header, "|---|---|---|---|---|---|"])
    for run in runs:
        lines.append(
            f"| {run.seed[:24]} | {run.repeat} | "
            f"{fmt(run.timings.get('voice_end_to_final_ms'))} | "
            f"{fmt(run.timings.get('voice_end_to_order_ms'))} | "
            f"{fmt(run.timings.get('order_to_first_audio_ms'))} | "
            f"{fmt(run.timings.get('final_to_first_audio_ms'))} |"
        )
    lines.extend(
        [
            "",
            "## Internal Stage Breakdown",
            "",
            "| seed | repeat | stt_ms | stt_partial_ms | tomoko_ms | tts_ms | total_ms |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for run in runs:
        stages = run.stage_timings_ms
        lines.append(
            f"| {run.seed[:24]} | {run.repeat} | "
            f"{fmt(stages.get('stt_ms'))} | "
            f"{fmt(stages.get('stt_partial_ms'))} | "
            f"{fmt(stages.get('tomoko_ms'))} | "
            f"{fmt(stages.get('tts_ms'))} | "
            f"{fmt(stages.get('total_ms'))} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws")
    parser.add_argument("--voice", default="Kyoko")
    parser.add_argument("--seeds", default="scripts/seeds/utterances.txt")
    parser.add_argument("--category", default="request")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--trailing-silence-ms", type=int, default=2500)
    parser.add_argument("--timeout-sec", type=float, default=45.0)
    parser.add_argument(
        "--reset-conversation",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Reset Tomoko realtime conversation state before each measured utterance.",
    )
    parser.add_argument("--output-dir", default="logs")
    parser.add_argument("--p50-target-ms", type=float)
    parser.add_argument("--p95-target-ms", type=float)
    parser.add_argument("--partial-p50-target-ms", type=float)
    args = parser.parse_args()

    targets = dict(DEFAULT_TARGETS)
    if args.p50_target_ms is not None:
        targets["final_origin_p50_ms"] = args.p50_target_ms
    if args.p95_target_ms is not None:
        targets["final_origin_p95_ms"] = args.p95_target_ms
    if args.partial_p50_target_ms is not None:
        targets["partial_origin_p50_ms"] = args.partial_p50_target_ms

    seed_lines = Path(args.seeds).read_text(encoding="utf-8").splitlines()
    seeds = select_seeds(
        seed_lines,
        count=args.count,
        category=args.category or None,
    )
    if not seeds:
        raise SystemExit(f"no seeds found in {args.seeds} (category={args.category})")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    audio: dict[str, tuple[float, ...]] = {}
    for index, seed in enumerate(seeds):
        wav_path = output_dir / f"latency-suite-{stamp}-seed{index}.wav"
        synthesize_say_wav(seed, args.voice, wav_path)
        sample_rate, samples = read_wav_float32(wav_path)
        if sample_rate != SAMPLE_RATE:
            raise ValueError(f"expected {SAMPLE_RATE}Hz WAV, got {sample_rate}")
        audio[seed] = samples

    runs: list[RunResult] = []
    for repeat in range(args.repeats):
        for seed in seeds:
            try:
                recorder, voice_end = asyncio.run(
                    run_one_utterance(
                        url=args.url,
                        samples=audio[seed],
                        trailing_silence_ms=args.trailing_silence_ms,
                        timeout_sec=args.timeout_sec,
                        reset_conversation=args.reset_conversation,
                    )
                )
            except Exception as exc:
                print(
                    f"[latency-suite] repeat={repeat} seed={seed[:24]!r} "
                    f"ERROR {type(exc).__name__}: {exc}",
                    flush=True,
                )
                runs.append(
                    RunResult(
                        seed=seed,
                        repeat=repeat,
                        first_audio_ms=None,
                        partial_origin=False,
                        reconciled=False,
                        false_early=False,
                        final_transcript="",
                        timings={},
                        stage_timings_ms={},
                        timeline=[],
                    )
                )
                continue
            classified = classify_run(
                recorder.timeline, voice_end_elapsed_ms=voice_end
            )
            finals = [
                str(entry.get("payload", {}).get("text", ""))
                for entry in recorder.timeline
                if entry.get("type") == "transcript"
                and entry.get("payload", {}).get("is_final")
            ]
            run = RunResult(
                seed=seed,
                repeat=repeat,
                first_audio_ms=classified["first_audio_ms"],
                partial_origin=classified["partial_origin"],
                reconciled=classified["reconciled"],
                false_early=classified["false_early"],
                final_transcript=finals[0] if finals else "",
                timings={
                    key: classified[key]
                    for key in (
                        "voice_end_elapsed_ms",
                        "first_final_elapsed_ms",
                        "first_order_elapsed_ms",
                        "first_audio_elapsed_ms",
                        "voice_end_to_final_ms",
                        "voice_end_to_order_ms",
                        "order_to_first_audio_ms",
                        "final_to_first_audio_ms",
                    )
                },
                stage_timings_ms=classified["stage_timings_ms"],
                timeline=list(recorder.timeline),
            )
            runs.append(run)
            first_audio_display = (
                "none"
                if run.first_audio_ms is None
                else f"{run.first_audio_ms:.1f}ms"
            )
            print(
                f"[latency-suite] repeat={repeat} seed={seed[:24]!r} "
                f"first_audio={first_audio_display} "
                f"origin={'partial' if run.partial_origin else 'final'} "
                f"reconciled={run.reconciled} false_early={run.false_early}",
                flush=True,
            )

    stats = summarize(runs)
    results = evaluate_targets(stats, targets)
    passed = all(result.ok for result in results)

    payload = {
        "stamp": stamp,
        "url": args.url,
        "category": args.category,
        "count": len(seeds),
        "repeats": args.repeats,
        "targets": targets,
        "reset_conversation": args.reset_conversation,
        "stats": stats,
        "assertions": [
            {"name": result.name, "ok": result.ok, "detail": result.detail}
            for result in results
        ],
        "runs": [
            {
                "seed": run.seed,
                "repeat": run.repeat,
                "first_audio_ms": run.first_audio_ms,
                "partial_origin": run.partial_origin,
                "reconciled": run.reconciled,
                "false_early": run.false_early,
                "final_transcript": run.final_transcript,
                "timings": run.timings,
                "stage_timings_ms": run.stage_timings_ms,
                "timeline": run.timeline,
            }
            for run in runs
        ],
        "passed": passed,
    }
    json_path = output_dir / f"latency-suite-{stamp}.json"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    md_path = output_dir / f"latency-suite-{stamp}.md"
    md_path.write_text(
        render_markdown(stats, results, runs, stamp=stamp), encoding="utf-8"
    )

    for result in results:
        marker = "PASS" if result.ok else "FAIL"
        print(f"[latency-suite] {marker} {result.name} ({result.detail})")
    print(f"[latency-suite] wrote {json_path}")
    print(f"[latency-suite] wrote {md_path}")
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
