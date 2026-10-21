from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from scripts.v2_say_latency_smoke import generate_say_wav, read_wav_float32
from server.audio.stt import AppleSpeechStreamingBackend
from server.shared.models import AudioSpeechSegment, SpeechSchedulerInput, utc_now
from server.tomoko.scheduler import SpeechScheduler
from server.tomoko.semantic import (
    SemanticSaturationJudge,
    create_default_distilled_saturation_backend,
)


@dataclass(slots=True)
class PartialProbeResult:
    offset_ms: int
    samples: int
    text: str
    stt_elapsed_ms: float
    saturation: float | None
    saturation_source: str | None
    saturation_elapsed_ms: float | None
    scheduler_action: str | None
    scheduler_score: float | None
    would_start_llm: bool
    estimated_decision_at_ms: float | None
    estimated_lead_before_full_final_ms: float | None
    replay_decision_elapsed_ms: float | None
    error: str | None = None


@dataclass(slots=True)
class SemanticEarlySmokeResult:
    text: str
    voice: str
    input_wav: str
    input_duration_ms: float
    semantic_backend: str
    semantic_model: str
    saturation_threshold: float
    estimation_mode: str
    final_text: str
    final_stt_elapsed_ms: float
    full_final_available_from_speech_start_ms: float
    first_ok_offset_ms: int | None
    first_ok_estimated_decision_at_ms: float | None
    first_ok_estimated_lead_before_full_final_ms: float | None
    estimated_decision_before_full_final: bool
    partials: list[PartialProbeResult] = field(default_factory=list)
    artifact_path: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Estimate whether distilled semantic saturation can allow LLM start "
            "before the full Apple Speech final STT is available."
        )
    )
    parser.add_argument("--text", default="トモコ、今日の予定を一言で教えて。")
    parser.add_argument("--voice", default="Kyoko")
    parser.add_argument("--offset-ms", action="append", type=int, dest="offsets")
    parser.add_argument("--threshold", type=float, default=0.75)
    parser.add_argument("--output-dir", default="logs")
    return parser.parse_args()


async def run_smoke(
    args: argparse.Namespace,
    output_dir: Path,
    stamp: str,
) -> SemanticEarlySmokeResult:
    wav_path = generate_say_wav(args.text, args.voice, output_dir, f"semantic-early-{stamp}")
    sample_rate, samples = read_wav_float32(wav_path)
    if sample_rate != 16000:
        raise ValueError(f"expected 16000Hz WAV, got {sample_rate}")

    input_duration_ms = len(samples) / sample_rate * 1000.0
    offsets = [
        offset
        for offset in (args.offsets or [800, 1200, 1600, 2000, 2400])
        if 0 < offset < input_duration_ms
    ]
    stt_backend = AppleSpeechStreamingBackend()
    saturation_backend = create_default_distilled_saturation_backend()
    judge = SemanticSaturationJudge(distilled_backend=saturation_backend)
    scheduler = SpeechScheduler()

    final_started = time.perf_counter()
    final_text = await transcribe_prefix(
        stt_backend,
        samples,
        sample_rate,
        input_duration_ms,
    )
    final_stt_elapsed_ms = (time.perf_counter() - final_started) * 1000.0
    full_final_available_from_speech_start_ms = input_duration_ms + final_stt_elapsed_ms

    partial_results: list[PartialProbeResult] = []
    replay_started = time.perf_counter()
    for offset_ms in offsets:
        sample_count = min(len(samples), int(sample_rate * offset_ms / 1000.0))
        partial_started = time.perf_counter()
        try:
            text = await transcribe_prefix(
                stt_backend,
                samples[:sample_count],
                sample_rate,
                offset_ms,
            )
            stt_elapsed_ms = (time.perf_counter() - partial_started) * 1000.0
            if not text:
                partial_results.append(
                    PartialProbeResult(
                        offset_ms=offset_ms,
                        samples=sample_count,
                        text="",
                        stt_elapsed_ms=stt_elapsed_ms,
                        saturation=None,
                        saturation_source=None,
                        saturation_elapsed_ms=None,
                        scheduler_action=None,
                        scheduler_score=None,
                        would_start_llm=False,
                        estimated_decision_at_ms=None,
                        estimated_lead_before_full_final_ms=None,
                        replay_decision_elapsed_ms=None,
                    )
                )
                continue

            saturation_started = time.perf_counter()
            saturation = await judge.judge(text, partial=True)
            saturation_elapsed_ms = (time.perf_counter() - saturation_started) * 1000.0
            scheduler_output = scheduler.decide(
                SpeechSchedulerInput(
                    partial_stt_text=text,
                    stable_prefix=text,
                    semantic_saturation=saturation.saturation,
                    p_yielding=0.95,
                )
            )
            would_start = (
                saturation.saturation >= args.threshold
                and scheduler_output.action != "suppress"
            )
            estimated_decision_at_ms = offset_ms + saturation_elapsed_ms
            lead_ms = full_final_available_from_speech_start_ms - estimated_decision_at_ms
            partial_results.append(
                PartialProbeResult(
                    offset_ms=offset_ms,
                    samples=sample_count,
                    text=text,
                    stt_elapsed_ms=stt_elapsed_ms,
                    saturation=saturation.saturation,
                    saturation_source=saturation.source,
                    saturation_elapsed_ms=saturation_elapsed_ms,
                    scheduler_action=scheduler_output.action.value,
                    scheduler_score=scheduler_output.score,
                    would_start_llm=would_start,
                    estimated_decision_at_ms=estimated_decision_at_ms,
                    estimated_lead_before_full_final_ms=lead_ms,
                    replay_decision_elapsed_ms=(time.perf_counter() - replay_started) * 1000.0,
                )
            )
        except Exception as exc:
            partial_results.append(
                PartialProbeResult(
                    offset_ms=offset_ms,
                    samples=sample_count,
                    text="",
                    stt_elapsed_ms=(time.perf_counter() - partial_started) * 1000.0,
                    saturation=None,
                    saturation_source=None,
                    saturation_elapsed_ms=None,
                    scheduler_action=None,
                    scheduler_score=None,
                    would_start_llm=False,
                    estimated_decision_at_ms=None,
                    estimated_lead_before_full_final_ms=None,
                    replay_decision_elapsed_ms=None,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )

    first_ok = next((item for item in partial_results if item.would_start_llm), None)
    result = SemanticEarlySmokeResult(
        text=args.text,
        voice=args.voice,
        input_wav=str(wav_path),
        input_duration_ms=input_duration_ms,
        semantic_backend="distilled_hash_ridge",
        semantic_model=str(saturation_backend.model_path),
        saturation_threshold=args.threshold,
        estimation_mode=(
            "prefix-window replay: assumes a real streaming STT partial with the same text "
            "is available at offset_ms; current Apple Speech sidecar still returns final-only."
        ),
        final_text=final_text,
        final_stt_elapsed_ms=final_stt_elapsed_ms,
        full_final_available_from_speech_start_ms=full_final_available_from_speech_start_ms,
        first_ok_offset_ms=first_ok.offset_ms if first_ok else None,
        first_ok_estimated_decision_at_ms=(
            first_ok.estimated_decision_at_ms if first_ok else None
        ),
        first_ok_estimated_lead_before_full_final_ms=(
            first_ok.estimated_lead_before_full_final_ms if first_ok else None
        ),
        estimated_decision_before_full_final=bool(
            first_ok and (first_ok.estimated_lead_before_full_final_ms or 0.0) > 0.0
        ),
        partials=partial_results,
    )
    return result


async def transcribe_prefix(
    backend: AppleSpeechStreamingBackend,
    samples: tuple[float, ...],
    sample_rate: int,
    duration_ms: float,
) -> str:
    now = utc_now()
    segment = AudioSpeechSegment(
        samples=samples,
        sample_rate=sample_rate,
        started_at=now,
        ended_at=now + timedelta(milliseconds=duration_ms),
    )
    async for event in backend.transcribe_stream(segment):
        if event.is_final:
            return event.text.strip()
    return ""


def append_latency_row(result: SemanticEarlySmokeResult) -> None:
    latency_path = Path("_docs/latency.md")
    if result.first_ok_estimated_lead_before_full_final_ms is None:
        metric = "no early OK"
    else:
        metric = (
            f"first OK at {result.first_ok_estimated_decision_at_ms:.1f}ms from speech start; "
            f"lead {result.first_ok_estimated_lead_before_full_final_ms:.1f}ms"
        )
    latency_path.write_text(
        latency_path.read_text(encoding="utf-8")
        + (
            f"| {datetime.now().strftime('%Y-%m-%d')} | "
            f"Tomoko v2 distilled semantic early-start smoke | "
            f"`say prefix windows -> Apple Speech -> distilled saturation` | "
            f"{metric} | final STT available at "
            f"{result.full_final_available_from_speech_start_ms:.1f}ms, artifact "
            f"`{result.artifact_path}`. |\n"
        ),
        encoding="utf-8",
    )


async def async_main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    result = await run_smoke(args, output_dir, stamp)
    output_path = output_dir / f"semantic-early-smoke-{stamp}.json"
    result.artifact_path = str(output_path)
    payload: dict[str, Any] = asdict(result)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    append_latency_row(result)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"wrote {output_path}")


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
