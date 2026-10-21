"""Scenario replay harness for Tomoko v2.

Replays scripted user speech (macOS ``say`` audio in real mode, energy bursts in
fake mode) into the hot-path ``/ws`` endpoint, records the full event timeline
as a JSON artifact, and asserts scenario expectations so that conversation
behavior can be verified without a human speaking into a microphone.

Usage:
    uv run python -m scripts.v2_scenario_replay --scenario scripts/scenarios/basic-reply.json
    uv run python -m scripts.v2_scenario_replay --suite scripts/scenarios --runtime fake
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import os
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

SAMPLE_RATE = 16000
REAL_CHUNK_SAMPLES = 128
FAKE_CHUNK_SAMPLES = 512


@dataclass(slots=True)
class ScenarioStep:
    say: str = ""
    event: dict[str, Any] | None = None
    cut_at_ratio: float | None = None
    pre_pause_ms: int = 0
    speech_ms: int = 400
    trailing_silence_ms: int = 2500
    overlap_during_playback: bool = False
    wait_for: str | None = "prompt_complete"
    wait_for_count: int = 1
    wait_timeout_sec: float = 45.0
    chunk_sleep_ms: float = 0.0
    expect: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ScenarioExpect:
    min_events: dict[str, int] = field(default_factory=dict)
    max_events: dict[str, int] = field(default_factory=dict)
    require_event_order: list[str] = field(default_factory=list)
    forbid_events: list[str] = field(default_factory=list)
    max_voice_end_to_first_audio_ms: float | None = None
    final_transcript_contains: str | None = None
    speech_order_modes: list[str] | None = None
    speech_order_modes_includes: list[str] | None = None
    speech_order_text_contains: str | None = None
    speech_order_reason_contains: str | None = None
    max_speech_order_text_chars: int | None = None
    score_breakdown_any: dict[str, Any] | list[dict[str, Any]] | None = None


@dataclass(slots=True)
class Scenario:
    name: str
    description: str = ""
    runtime: str = "any"
    fake_transcript: str | None = None
    fake_reply: str | None = None
    fake_stt_events: list[dict[str, Any]] | None = None
    fake_calendar: list[dict[str, Any]] | None = None
    fake_personality: dict[str, Any] | None = None
    fake_user_status: dict[str, Any] | None = None
    fake_candidates: list[dict[str, Any]] | None = None
    fake_world_info: list[dict[str, Any]] | None = None
    fake_sense: dict[str, Any] | None = None
    steps: list[ScenarioStep] = field(default_factory=list)
    expect: ScenarioExpect = field(default_factory=ScenarioExpect)


@dataclass(slots=True)
class AssertionResult:
    name: str
    ok: bool
    detail: str


def load_scenario(path: Path) -> Scenario:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    steps_payload = payload.get("steps")
    if not isinstance(steps_payload, list) or not steps_payload:
        raise ValueError(f"scenario {path} requires a non-empty steps list")
    steps = []
    for raw in steps_payload:
        if "say" not in raw and "event" not in raw:
            raise ValueError(f"scenario {path} has a step without 'say' or 'event'")
        event = raw.get("event")
        if event is not None and not isinstance(event, dict):
            raise ValueError(f"scenario {path} has a non-object event step")
        steps.append(
            ScenarioStep(
                say=str(raw.get("say", "")),
                event=event,
                cut_at_ratio=raw.get("cut_at_ratio"),
                pre_pause_ms=int(raw.get("pre_pause_ms", 0)),
                speech_ms=int(raw.get("speech_ms", 400)),
                trailing_silence_ms=int(raw.get("trailing_silence_ms", 2500)),
                overlap_during_playback=bool(raw.get("overlap_during_playback", False)),
                wait_for=raw.get("wait_for", "prompt_complete"),
                wait_for_count=int(raw.get("wait_for_count", 1)),
                wait_timeout_sec=float(raw.get("wait_timeout_sec", 45.0)),
                chunk_sleep_ms=float(raw.get("chunk_sleep_ms", 0.0)),
                expect=dict(raw.get("expect", {})),
            )
        )
    expect_payload = payload.get("expect", {})
    expect = ScenarioExpect(
        min_events=dict(expect_payload.get("min_events", {})),
        max_events=dict(expect_payload.get("max_events", {})),
        require_event_order=list(expect_payload.get("require_event_order", [])),
        forbid_events=list(expect_payload.get("forbid_events", [])),
        max_voice_end_to_first_audio_ms=expect_payload.get("max_voice_end_to_first_audio_ms"),
        final_transcript_contains=expect_payload.get("final_transcript_contains"),
        speech_order_modes=expect_payload.get("speech_order_modes"),
        speech_order_modes_includes=expect_payload.get("speech_order_modes_includes"),
        speech_order_text_contains=expect_payload.get("speech_order_text_contains"),
        speech_order_reason_contains=expect_payload.get("speech_order_reason_contains"),
        max_speech_order_text_chars=expect_payload.get("max_speech_order_text_chars"),
        score_breakdown_any=expect_payload.get("score_breakdown_any"),
    )
    return Scenario(
        name=str(payload.get("name", Path(path).stem)),
        description=str(payload.get("description", "")),
        runtime=str(payload.get("runtime", "any")),
        fake_transcript=payload.get("fake_transcript"),
        fake_reply=payload.get("fake_reply"),
        fake_stt_events=payload.get("fake_stt_events"),
        fake_calendar=payload.get("fake_calendar"),
        fake_personality=payload.get("fake_personality"),
        fake_user_status=payload.get("fake_user_status"),
        fake_candidates=payload.get("fake_candidates"),
        fake_world_info=payload.get("fake_world_info"),
        fake_sense=payload.get("fake_sense"),
        steps=steps,
        expect=expect,
    )


def scenario_control_events(scenario: Scenario) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [
        {"type": "latency_control", "command": "reset_conversation"}
    ]
    if scenario.fake_calendar:
        events.append(
            {
                "type": "latency_control",
                "command": "set_fake_calendar",
                "items": scenario.fake_calendar,
            }
        )
    return events


def apply_cut_ratio(
    samples: tuple[float, ...],
    cut_at_ratio: float | None,
) -> tuple[float, ...]:
    if cut_at_ratio is None:
        return samples
    if not 0.0 < cut_at_ratio <= 1.0:
        raise ValueError(f"cut_at_ratio must be in (0.0, 1.0], got {cut_at_ratio}")
    return samples[: int(len(samples) * cut_at_ratio)]


def is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    position = 0
    for item in haystack:
        if position < len(needle) and item == needle[position]:
            position += 1
    return position == len(needle)


def _expect_dict(expect: ScenarioExpect | dict[str, Any]) -> dict[str, Any]:
    if isinstance(expect, ScenarioExpect):
        return {
            "min_events": expect.min_events,
            "max_events": expect.max_events,
            "require_event_order": expect.require_event_order,
            "forbid_events": expect.forbid_events,
            "max_voice_end_to_first_audio_ms": expect.max_voice_end_to_first_audio_ms,
            "final_transcript_contains": expect.final_transcript_contains,
            "speech_order_modes": expect.speech_order_modes,
            "speech_order_modes_includes": expect.speech_order_modes_includes,
            "speech_order_text_contains": expect.speech_order_text_contains,
            "speech_order_reason_contains": expect.speech_order_reason_contains,
            "max_speech_order_text_chars": expect.max_speech_order_text_chars,
            "score_breakdown_any": expect.score_breakdown_any,
        }
    return expect


def evaluate_expectations(
    expect: ScenarioExpect | dict[str, Any],
    artifact: dict[str, Any],
    prefix: str = "",
) -> list[AssertionResult]:
    spec = _expect_dict(expect)
    counts: dict[str, int] = dict(artifact.get("event_counts", {}))
    timeline: list[dict[str, Any]] = list(artifact.get("timeline", []))
    types = [str(entry.get("type")) for entry in timeline]
    results: list[AssertionResult] = []

    def add(name: str, ok: bool, detail: str) -> None:
        results.append(AssertionResult(name=f"{prefix}{name}", ok=ok, detail=detail))

    for event_type, minimum in dict(spec.get("min_events") or {}).items():
        actual = counts.get(event_type, 0)
        add(
            f"min_events[{event_type}]>={minimum}",
            actual >= int(minimum),
            f"actual={actual}",
        )
    for event_type, maximum in dict(spec.get("max_events") or {}).items():
        actual = counts.get(event_type, 0)
        add(
            f"max_events[{event_type}]<={maximum}",
            actual <= int(maximum),
            f"actual={actual}",
        )
    order = list(spec.get("require_event_order") or [])
    if order:
        add(
            f"require_event_order={order}",
            is_subsequence(order, types),
            f"observed={types}",
        )
    for event_type in list(spec.get("forbid_events") or []):
        actual = counts.get(event_type, 0)
        add(f"forbid_events[{event_type}]", actual == 0, f"actual={actual}")

    latency_bound = spec.get("max_voice_end_to_first_audio_ms")
    if latency_bound is not None:
        measured = [
            (index, step.get("voice_end_to_first_audio_ms"))
            for index, step in enumerate(artifact.get("steps", []))
            if step.get("voice_end_to_first_audio_ms") is not None
        ]
        if not measured:
            add(
                f"max_voice_end_to_first_audio_ms<={latency_bound}",
                False,
                "no step measured first audio",
            )
        for index, value in measured:
            add(
                f"step[{index}].voice_end_to_first_audio_ms<={latency_bound}",
                float(value) <= float(latency_bound),
                f"actual={float(value):.1f}ms",
            )

    contains = spec.get("final_transcript_contains")
    if contains is not None:
        finals = [
            str(entry.get("payload", {}).get("text", ""))
            for entry in timeline
            if entry.get("type") == "transcript" and entry.get("payload", {}).get("is_final")
        ]
        add(
            f"final_transcript_contains[{contains}]",
            any(contains in text for text in finals),
            f"finals={finals}",
        )

    modes = spec.get("speech_order_modes")
    includes = spec.get("speech_order_modes_includes")
    speech_orders = [
        dict(entry.get("payload", {}))
        for entry in timeline
        if entry.get("type") == "speech_order"
    ]
    if modes is not None or includes is not None:
        observed = [str(payload.get("mode")) for payload in speech_orders]
        if modes is not None:
            add(
                f"speech_order_modes=={list(modes)}",
                observed == list(modes),
                f"observed={observed}",
            )
        if includes is not None:
            for mode in includes:
                add(
                    f"speech_order_modes_includes[{mode}]",
                    mode in observed,
                    f"observed={observed}",
                )
    text_contains = spec.get("speech_order_text_contains")
    if text_contains is not None:
        observed_texts = [str(payload.get("text", "")) for payload in speech_orders]
        add(
            f"speech_order_text_contains[{text_contains}]",
            any(str(text_contains) in text for text in observed_texts),
            f"observed={observed_texts}",
        )
    reason_contains = spec.get("speech_order_reason_contains")
    if reason_contains is not None:
        observed_reasons = [str(payload.get("reason", "")) for payload in speech_orders]
        add(
            f"speech_order_reason_contains[{reason_contains}]",
            any(str(reason_contains) in reason for reason in observed_reasons),
            f"observed={observed_reasons}",
        )
    max_chars = spec.get("max_speech_order_text_chars")
    if max_chars is not None:
        observed_lengths = [
            len(str(payload.get("text", ""))) for payload in speech_orders
        ]
        add(
            f"max_speech_order_text_chars<={max_chars}",
            bool(observed_lengths)
            and any(length <= int(max_chars) for length in observed_lengths),
            f"observed={observed_lengths}",
        )
    for index, score_spec in enumerate(_score_breakdown_specs(spec.get("score_breakdown_any"))):
        decisions = [
            entry.get("payload", {}).get("score_breakdown", {})
            for entry in timeline
            if entry.get("type") == "scheduler_decision"
        ]
        matched = [
            breakdown
            for breakdown in decisions
            if _score_breakdown_matches(dict(breakdown), score_spec)
        ]
        add(
            f"score_breakdown_any[{index}]",
            bool(matched),
            _score_breakdown_detail(decisions, score_spec),
        )
    return results


def _score_breakdown_specs(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [dict(item) for item in raw]
    return [dict(raw)]


def _score_breakdown_matches(
    breakdown: dict[str, Any],
    spec: dict[str, Any],
) -> bool:
    return all(
        _score_value_matches(breakdown.get(key), condition)
        for key, condition in spec.items()
    )


def _score_value_matches(actual_raw: Any, condition: Any) -> bool:
    if actual_raw is None:
        return False
    actual = float(actual_raw)
    if isinstance(condition, dict):
        tolerance = float(condition.get("tolerance", 1e-6))
        if "min" in condition and actual < float(condition["min"]) - tolerance:
            return False
        if "max" in condition and actual > float(condition["max"]) + tolerance:
            return False
        if "equals" in condition and abs(actual - float(condition["equals"])) > tolerance:
            return False
        return True
    return abs(actual - float(condition)) <= 1e-6


def _score_breakdown_detail(
    decisions: list[Any],
    spec: dict[str, Any],
) -> str:
    observed = [
        {key: dict(decision).get(key) for key in spec}
        for decision in decisions
        if isinstance(decision, dict)
    ]
    return f"spec={spec} observed={observed}"


def evaluate_step_expectations(artifact: dict[str, Any]) -> list[AssertionResult]:
    results: list[AssertionResult] = []
    steps: list[dict[str, Any]] = list(artifact.get("steps", []))
    timeline: list[dict[str, Any]] = list(artifact.get("timeline", []))
    for index, step in enumerate(steps):
        expect = step.get("expect") or {}
        if not expect:
            continue
        window_start = float(step.get("voice_start_elapsed_ms", 0.0))
        next_steps = steps[index + 1 :]
        window_end = (
            float(next_steps[0]["voice_start_elapsed_ms"])
            if next_steps and next_steps[0].get("voice_start_elapsed_ms") is not None
            else math.inf
        )
        window = [
            entry
            for entry in timeline
            if window_start <= float(entry.get("elapsed_ms", 0.0)) < window_end
        ]
        counts: dict[str, int] = {}
        for entry in window:
            key = str(entry.get("type"))
            counts[key] = counts.get(key, 0) + 1
        sub_artifact = {
            "event_counts": counts,
            "timeline": window,
            "steps": [step],
        }
        results.extend(
            evaluate_expectations(expect, sub_artifact, prefix=f"step[{index}].")
        )
    return results


def read_wav_float32(path: Path) -> tuple[int, tuple[float, ...]]:
    import wave

    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        sample_width = wav.getsampwidth()
        frames = wav.readframes(wav.getnframes())
    if channels != 1:
        raise ValueError(f"expected mono WAV, got {channels} channels")
    if sample_width != 2:
        raise ValueError(f"expected 16-bit WAV, got sample width {sample_width}")
    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
    return sample_rate, tuple(max(-1.0, min(1.0, sample / 32768.0)) for sample in samples)


def synthesize_say_wav(text: str, voice: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp_dir:
        aiff_path = Path(temp_dir) / "input.aiff"
        subprocess.run(["say", "-v", voice, "-o", str(aiff_path), text], check=True, text=True)
        subprocess.run(
            [
                "afconvert",
                "-f",
                "WAVE",
                "-d",
                "LEI16@16000",
                "-c",
                "1",
                str(aiff_path),
                str(output_path),
            ],
            check=True,
            text=True,
        )
    return output_path


def pack_float32(samples: tuple[float, ...]) -> bytes:
    if not samples:
        return b""
    return struct.pack(f"<{len(samples)}f", *samples)


class TimelineRecorder:
    def __init__(self) -> None:
        self.timeline: list[dict[str, Any]] = []
        self.event_counts: dict[str, int] = {}
        self.started_at = time.perf_counter()

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started_at) * 1000.0

    def record(self, event_type: str, payload: dict[str, Any] | None = None, **extra: Any) -> None:
        self.event_counts[event_type] = self.event_counts.get(event_type, 0) + 1
        entry: dict[str, Any] = {"elapsed_ms": self.elapsed_ms(), "type": event_type}
        if payload is not None:
            entry["payload"] = payload
        entry.update(extra)
        self.timeline.append(entry)

    def events_after(self, event_type: str, after_elapsed_ms: float) -> list[dict[str, Any]]:
        # "speech_order:stop" のように ":<mode>" を付けると payload.mode でも絞り込む
        wanted_type, _, wanted_mode = event_type.partition(":")
        return [
            entry
            for entry in self.timeline
            if entry["type"] == wanted_type
            and entry["elapsed_ms"] >= after_elapsed_ms
            and (
                not wanted_mode
                or entry.get("payload", {}).get("mode") == wanted_mode
            )
        ]


async def _receiver(websocket: Any, recorder: TimelineRecorder) -> None:
    async for message in websocket:
        if isinstance(message, bytes):
            recorder.record("binary_audio", bytes=len(message))
            continue
        payload = json.loads(message)
        event_type = str(payload.get("type", "unknown"))
        if event_type == "debug_marker":
            recorder.event_counts[event_type] = recorder.event_counts.get(event_type, 0) + 1
            continue
        recorder.record(
            event_type,
            payload={key: value for key, value in payload.items() if key != "type"},
        )


async def _wait_for_event(
    recorder: TimelineRecorder,
    event_type: str,
    after_elapsed_ms: float,
    timeout_sec: float,
    count: int = 1,
) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if len(recorder.events_after(event_type, after_elapsed_ms)) >= count:
            return True
        await asyncio.sleep(0.02)
    return False


async def _stream_samples(
    websocket: Any,
    samples: tuple[float, ...],
    chunk_samples: int,
    sleep_sec_per_chunk: float,
) -> None:
    for offset in range(0, len(samples), chunk_samples):
        chunk = samples[offset : offset + chunk_samples]
        await websocket.send(pack_float32(chunk))
        if sleep_sec_per_chunk > 0:
            await asyncio.sleep(sleep_sec_per_chunk)


async def run_scenario(
    scenario: Scenario,
    *,
    runtime_mode: str,
    url: str,
    voice: str,
    output_dir: Path,
    stamp: str,
) -> dict[str, Any]:
    from websockets.asyncio.client import connect

    realtime = runtime_mode == "real"
    step_audio: list[tuple[float, ...]] = []
    for index, step in enumerate(scenario.steps):
        if step.event is not None:
            step_audio.append(())
            continue
        if realtime:
            wav_path = output_dir / f"scenario-{scenario.name}-{stamp}-step{index}.wav"
            synthesize_say_wav(step.say, voice, wav_path)
            sample_rate, samples = read_wav_float32(wav_path)
            if sample_rate != SAMPLE_RATE:
                raise ValueError(f"expected {SAMPLE_RATE}Hz WAV, got {sample_rate}")
            step_audio.append(apply_cut_ratio(samples, step.cut_at_ratio))
        else:
            speech_samples = int(SAMPLE_RATE * step.speech_ms / 1000)
            samples = (0.2,) * speech_samples
            step_audio.append(apply_cut_ratio(samples, step.cut_at_ratio))

    recorder = TimelineRecorder()
    step_records: list[dict[str, Any]] = []
    control_records: list[dict[str, Any]] = []
    chunk_samples = REAL_CHUNK_SAMPLES if realtime else FAKE_CHUNK_SAMPLES

    async with connect(url, max_size=None) as websocket:
        receive_task = asyncio.create_task(_receiver(websocket, recorder))
        try:
            if realtime:
                for event in scenario_control_events(scenario):
                    start_elapsed_ms = recorder.elapsed_ms()
                    await websocket.send(json.dumps(event, ensure_ascii=False))
                    seen = await _wait_for_event(
                        recorder,
                        "latency_control_ack",
                        start_elapsed_ms,
                        timeout_sec=5.0,
                    )
                    control_records.append(
                        {
                            "event": event,
                            "start_elapsed_ms": start_elapsed_ms,
                            "ack_seen": seen,
                        }
                    )
            for index, step in enumerate(scenario.steps):
                record: dict[str, Any] = {
                    "index": index,
                    "say": step.say,
                    "event": step.event,
                    "cut_at_ratio": step.cut_at_ratio,
                    "expect": step.expect,
                }
                sleep_per_chunk = (
                    chunk_samples / SAMPLE_RATE
                    if realtime
                    else step.chunk_sleep_ms / 1000.0
                )
                if step.event is not None:
                    record["event_start_elapsed_ms"] = recorder.elapsed_ms()
                    await websocket.send(json.dumps(step.event, ensure_ascii=False))
                    event_end = recorder.elapsed_ms()
                    record["voice_start_elapsed_ms"] = event_end
                    record["voice_end_elapsed_ms"] = event_end
                    if step.wait_for:
                        seen = await _wait_for_event(
                            recorder,
                            step.wait_for,
                            event_end,
                            step.wait_timeout_sec,
                            count=step.wait_for_count,
                        )
                        record["wait_for"] = step.wait_for
                        record["wait_for_seen"] = seen
                    audio_after = recorder.events_after("binary_audio", event_end)
                    record["voice_end_to_first_audio_ms"] = (
                        audio_after[0]["elapsed_ms"] - event_end
                        if audio_after
                        else None
                    )
                    step_records.append(record)
                    continue
                if step.overlap_during_playback:
                    previous_end = (
                        step_records[-1]["voice_end_elapsed_ms"] if step_records else 0.0
                    )
                    seen = await _wait_for_event(
                        recorder, "binary_audio", previous_end, step.wait_timeout_sec
                    )
                    record["overlap_playback_seen"] = seen
                elif step.pre_pause_ms > 0:
                    pause_samples = int(SAMPLE_RATE * step.pre_pause_ms / 1000)
                    silence = (0.0,) * pause_samples
                    await _stream_samples(websocket, silence, chunk_samples, sleep_per_chunk)

                record["voice_start_elapsed_ms"] = recorder.elapsed_ms()
                await _stream_samples(
                    websocket, step_audio[index], chunk_samples, sleep_per_chunk
                )
                voice_end = recorder.elapsed_ms()
                record["voice_end_elapsed_ms"] = voice_end

                trailing_samples = int(SAMPLE_RATE * step.trailing_silence_ms / 1000)
                silence_chunk = (0.0,) * chunk_samples
                silence_chunks = math.ceil(trailing_samples / chunk_samples)
                for _ in range(silence_chunks):
                    if step.wait_for and (
                        len(recorder.events_after(step.wait_for, voice_end))
                        >= step.wait_for_count
                    ):
                        break
                    await websocket.send(pack_float32(silence_chunk))
                    if sleep_per_chunk > 0:
                        await asyncio.sleep(sleep_per_chunk)

                if step.wait_for:
                    seen = await _wait_for_event(
                        recorder,
                        step.wait_for,
                        voice_end,
                        step.wait_timeout_sec,
                        count=step.wait_for_count,
                    )
                    record["wait_for"] = step.wait_for
                    record["wait_for_seen"] = seen

                audio_after = recorder.events_after("binary_audio", voice_end)
                record["voice_end_to_first_audio_ms"] = (
                    audio_after[0]["elapsed_ms"] - voice_end if audio_after else None
                )
                step_records.append(record)
        finally:
            await websocket.close()
            receive_task.cancel()
            try:
                await receive_task
            except asyncio.CancelledError:
                pass

    return {
        "scenario_name": scenario.name,
        "description": scenario.description,
        "runtime_mode": runtime_mode,
        "url": url,
        "control_events": control_records,
        "steps": step_records,
        "event_counts": recorder.event_counts,
        "timeline": recorder.timeline,
    }


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_http_ready(url: str, timeout_sec: float = 15.0) -> None:
    import httpx

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=1.0)
        except httpx.HTTPError:
            time.sleep(0.1)
            continue
        if response.status_code < 500:
            return
        time.sleep(0.1)
    raise RuntimeError(f"server did not become ready: {url}")


class FakeRuntimeProcesses:
    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.port = _free_port()
        self.tomoko_port = _free_port()
        self.processes: list[subprocess.Popen[str]] = []

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws"

    def __enter__(self) -> FakeRuntimeProcesses:
        env = os.environ.copy()
        env["TOMOKO_V2_FAKE_RUNTIME"] = "1"
        env["TOMOKO_V2_WS_SPLIT"] = "1"
        env["TOMOKO_INTERNAL_WS_URL"] = f"ws://127.0.0.1:{self.tomoko_port}/internal/hot-path"
        fake_transcript = self.scenario.fake_transcript or self.scenario.steps[0].say
        env["TOMOKO_V2_FAKE_TRANSCRIPT"] = fake_transcript
        if self.scenario.fake_reply:
            env["TOMOKO_V2_FAKE_REPLY"] = self.scenario.fake_reply
        if self.scenario.fake_stt_events:
            env["TOMOKO_V2_FAKE_STT_EVENTS"] = json.dumps(
                self.scenario.fake_stt_events, ensure_ascii=False
            )
        if self.scenario.fake_calendar:
            env["TOMOKO_V2_FAKE_CALENDAR"] = json.dumps(
                self.scenario.fake_calendar, ensure_ascii=False
            )
        if self.scenario.fake_personality:
            env["TOMOKO_V2_FAKE_PERSONALITY"] = json.dumps(
                self.scenario.fake_personality,
                ensure_ascii=False,
            )
        if self.scenario.fake_user_status:
            env["TOMOKO_V2_FAKE_USER_STATUS"] = json.dumps(
                self.scenario.fake_user_status,
                ensure_ascii=False,
            )
        if self.scenario.fake_candidates:
            env["TOMOKO_V2_FAKE_CANDIDATES"] = json.dumps(
                self.scenario.fake_candidates,
                ensure_ascii=False,
            )
        if self.scenario.fake_world_info:
            env["TOMOKO_V2_FAKE_WORLD_INFO"] = json.dumps(
                self.scenario.fake_world_info,
                ensure_ascii=False,
            )
        if self.scenario.fake_sense:
            env["TOMOKO_V2_FAKE_SENSE"] = json.dumps(
                self.scenario.fake_sense,
                ensure_ascii=False,
            )
        Path("logs").mkdir(exist_ok=True)
        tomoko = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "server.tomoko.realtime:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.tomoko_port),
                "--log-level",
                "warning",
            ],
            env=env,
            text=True,
        )
        self.processes.append(tomoko)
        _wait_http_ready(f"http://127.0.0.1:{self.tomoko_port}/docs")
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "server.hot_path.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--log-level",
                "warning",
            ],
            env=env,
            text=True,
        )
        self.processes.append(server)
        _wait_http_ready(f"http://127.0.0.1:{self.port}/")
        return self

    def __exit__(self, *exc_info: object) -> None:
        for process in self.processes:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
        for process in self.processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def _scenario_deadline_sec(scenario: Scenario) -> float:
    # ステップ待機の合計 + 音声再生/セットアップ余裕。ハングしても suite を止めない。
    return 120.0 + sum(step.wait_timeout_sec for step in scenario.steps)


def _run_scenario_with_deadline(scenario: Scenario, **kwargs: Any) -> dict[str, Any]:
    deadline = _scenario_deadline_sec(scenario)

    async def _guarded() -> dict[str, Any]:
        task = asyncio.ensure_future(run_scenario(scenario, **kwargs))
        done, pending = await asyncio.wait({task}, timeout=deadline)
        if pending:
            print(
                f"[scenario:{scenario.name}] WATCHDOG timeout after {deadline:.0f}s; "
                "dumping task stacks",
                file=sys.stderr,
                flush=True,
            )
            for stuck in asyncio.all_tasks():
                stuck.print_stack(limit=12, file=sys.stderr)
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
            raise RuntimeError(
                f"scenario {scenario.name} timed out after {deadline:.0f}s"
            )
        return task.result()

    return asyncio.run(_guarded())


def run_one(
    scenario_path: Path,
    *,
    runtime_arg: str | None,
    url: str,
    voice: str,
    output_dir: Path,
) -> tuple[bool, Path | None]:
    scenario = load_scenario(scenario_path)
    runtime_mode = runtime_arg or (scenario.runtime if scenario.runtime != "any" else "fake")
    if scenario.runtime != "any" and runtime_mode != scenario.runtime:
        print(f"[scenario:{scenario.name}] SKIP (requires runtime={scenario.runtime})")
        return True, None

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)

    if runtime_mode == "fake":
        with FakeRuntimeProcesses(scenario) as processes:
            artifact = _run_scenario_with_deadline(
                scenario,
                runtime_mode="fake",
                url=processes.url,
                voice=voice,
                output_dir=output_dir,
                stamp=stamp,
            )
    else:
        artifact = _run_scenario_with_deadline(
            scenario,
            runtime_mode="real",
            url=url,
            voice=voice,
            output_dir=output_dir,
            stamp=stamp,
        )

    assertions = evaluate_expectations(scenario.expect, artifact)
    assertions.extend(evaluate_step_expectations(artifact))
    passed = all(result.ok for result in assertions)
    artifact["assertions"] = [
        {"name": result.name, "ok": result.ok, "detail": result.detail} for result in assertions
    ]
    artifact["passed"] = passed

    artifact_path = output_dir / f"scenario-{scenario.name}-{stamp}.json"
    artifact_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for result in assertions:
        marker = "PASS" if result.ok else "FAIL"
        print(f"[scenario:{scenario.name}] {marker} {result.name} ({result.detail})")
    print(f"[scenario:{scenario.name}] {'PASS' if passed else 'FAIL'} -> {artifact_path}")
    return passed, artifact_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", help="path to a scenario JSON file")
    parser.add_argument("--suite", help="directory of scenario JSON files")
    parser.add_argument("--runtime", choices=["fake", "real"])
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws")
    parser.add_argument("--voice", default="Kyoko")
    parser.add_argument("--output-dir", default="logs")
    args = parser.parse_args()

    if bool(args.scenario) == bool(args.suite):
        parser.error("exactly one of --scenario or --suite is required")

    paths = (
        [Path(args.scenario)]
        if args.scenario
        else sorted(Path(args.suite).glob("*.json"))
    )
    if not paths:
        parser.error(f"no scenario files found in {args.suite}")

    all_passed = True
    for path in paths:
        try:
            passed, _ = run_one(
                path,
                runtime_arg=args.runtime,
                url=args.url,
                voice=args.voice,
                output_dir=Path(args.output_dir),
            )
        except Exception as exc:
            # 1 シナリオのハング/クラッシュで suite 全体を止めない
            print(f"[scenario:{path.stem}] FAIL (exception: {exc})", flush=True)
            passed = False
        all_passed = all_passed and passed
    raise SystemExit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
