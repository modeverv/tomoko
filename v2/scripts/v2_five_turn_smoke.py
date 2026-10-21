from __future__ import annotations

import argparse
import asyncio
import json
import math
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from websockets.asyncio.client import connect

from scripts.v2_say_latency_smoke import (
    generate_say_wav,
    pack_float32,
    read_wav_float32,
)

DEFAULT_TURNS = (
    "トモコ、短く返事して。",
    "今の返事、ちゃんと聞こえてる？",
    "今日の予定を一言で教えて。",
    "ありがとう。さっきの話に戻って、もう一言だけ。",
    "最後に、今の状態を短くまとめて。",
)


@dataclass(slots=True)
class FiveTurnResult:
    url: str
    voice: str
    chunk_samples: int
    trailing_silence_ms: int
    inter_turn_pause_ms: int
    turns: list[TurnResult]
    average_first_audio_ms: float | None
    p95_first_audio_ms: float | None
    max_first_audio_ms: float | None
    total_elapsed_ms: float
    artifact_path: str = ""


@dataclass(slots=True)
class TurnResult:
    index: int
    text: str
    input_wav: str
    input_duration_ms: float
    voice_end_to_transcript_ms: float | None = None
    voice_end_to_tts_result_ms: float | None = None
    voice_end_to_first_audio_ms: float | None = None
    first_audio_bytes: int | None = None
    final_transcript: str | None = None
    llm_prompt: str | None = None
    model_text: str | None = None
    tts_text: str | None = None
    audio_chunks: int = 0
    event_counts: dict[str, int] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class TurnState:
    result: TurnResult
    voice_end_at: float | None = None
    first_transcript_at: float | None = None
    first_tts_result_at: float | None = None
    first_audio_at: float | None = None
    first_audio_seen: asyncio.Event = field(default_factory=asyncio.Event)
    prompt_done: asyncio.Event = field(default_factory=asyncio.Event)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a 5-turn real runtime Tomoko v2 /ws smoke with macOS say audio."
    )
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws")
    parser.add_argument("--voice", default="Kyoko")
    parser.add_argument("--turn", action="append", dest="turns")
    parser.add_argument("--chunk-samples", type=int, default=128)
    parser.add_argument("--trailing-silence-ms", type=int, default=2500)
    parser.add_argument("--inter-turn-pause-ms", type=int, default=3000)
    parser.add_argument("--timeout-sec", type=float, default=60.0)
    parser.add_argument("--output-dir", default="logs")
    parser.add_argument("--start-server", action=argparse.BooleanOptionalAction, default=False)
    return parser.parse_args()


async def run_smoke(args: argparse.Namespace, output_dir: Path, stamp: str) -> FiveTurnResult:
    texts = tuple(args.turns) if args.turns else DEFAULT_TURNS
    wavs = [
        generate_say_wav(text, args.voice, output_dir, f"five-turn-{stamp}-turn{index}")
        for index, text in enumerate(texts, start=1)
    ]
    started_at = time.perf_counter()
    states: list[TurnState] = []
    current_state: TurnState | None = None
    receiver_started_at = time.perf_counter()

    async def receiver(websocket: Any) -> None:
        nonlocal current_state
        async for message in websocket:
            now = time.perf_counter()
            state = current_state
            if state is None:
                continue
            result = state.result
            if isinstance(message, bytes):
                result.audio_chunks += 1
                if state.first_audio_at is None:
                    state.first_audio_at = now
                    result.first_audio_bytes = len(message)
                    state.first_audio_seen.set()
                result.event_counts["binary_audio"] = result.event_counts.get("binary_audio", 0) + 1
                continue
            payload = json.loads(message)
            event_type = str(payload.get("type", "unknown"))
            result.event_counts[event_type] = result.event_counts.get(event_type, 0) + 1
            if event_type != "debug_marker":
                result.events.append(
                    {
                        "elapsed_ms": (now - receiver_started_at) * 1000.0,
                        "payload": payload,
                    }
                )
            if event_type == "transcript" and payload.get("is_final"):
                result.final_transcript = str(payload.get("text", ""))
                if state.first_transcript_at is None:
                    state.first_transcript_at = now
            elif event_type == "llm_prompt":
                result.llm_prompt = str(payload.get("prompt_text", ""))
            elif event_type == "model_complete":
                result.model_text = str(payload.get("text", ""))
            elif event_type == "tts_result":
                result.tts_text = str(payload.get("text", ""))
                if state.first_tts_result_at is None:
                    state.first_tts_result_at = now
            elif event_type == "prompt_complete":
                state.prompt_done.set()

    async with connect(args.url, max_size=None) as websocket:
        receiver_task = asyncio.create_task(receiver(websocket))
        try:
            for index, (text, wav_path) in enumerate(zip(texts, wavs, strict=True), start=1):
                sample_rate, samples = read_wav_float32(wav_path)
                if sample_rate != 16000:
                    raise ValueError(f"expected 16000Hz WAV, got {sample_rate}")
                result = TurnResult(
                    index=index,
                    text=text,
                    input_wav=str(wav_path),
                    input_duration_ms=len(samples) / sample_rate * 1000.0,
                )
                state = TurnState(result=result)
                states.append(state)
                current_state = state
                await send_turn_audio(websocket, samples, sample_rate, args.chunk_samples)
                state.voice_end_at = time.perf_counter()
                await send_silence_until_first_audio(websocket, state, sample_rate, args)
                await wait_for_turn_completion(state, args.timeout_sec)
                finalize_turn(state)
                if index != len(texts):
                    await asyncio.sleep(args.inter_turn_pause_ms / 1000.0)
        finally:
            current_state = None
            await websocket.close()
            receiver_task.cancel()
            try:
                await receiver_task
            except asyncio.CancelledError:
                pass

    first_audio_values = [
        state.result.voice_end_to_first_audio_ms
        for state in states
        if state.result.voice_end_to_first_audio_ms is not None
    ]
    return FiveTurnResult(
        url=args.url,
        voice=args.voice,
        chunk_samples=args.chunk_samples,
        trailing_silence_ms=args.trailing_silence_ms,
        inter_turn_pause_ms=args.inter_turn_pause_ms,
        turns=[state.result for state in states],
        average_first_audio_ms=average(first_audio_values),
        p95_first_audio_ms=percentile(first_audio_values, 0.95),
        max_first_audio_ms=max(first_audio_values) if first_audio_values else None,
        total_elapsed_ms=(time.perf_counter() - started_at) * 1000.0,
    )


async def send_turn_audio(
    websocket: Any,
    samples: tuple[float, ...],
    sample_rate: int,
    chunk_samples: int,
) -> None:
    for offset in range(0, len(samples), chunk_samples):
        chunk = samples[offset : offset + chunk_samples]
        await websocket.send(pack_float32(chunk))
        await asyncio.sleep(len(chunk) / sample_rate)


async def send_silence_until_first_audio(
    websocket: Any,
    state: TurnState,
    sample_rate: int,
    args: argparse.Namespace,
) -> None:
    silence_samples = int(sample_rate * args.trailing_silence_ms / 1000)
    silence_chunk = (0.0,) * args.chunk_samples
    silence_chunks = math.ceil(silence_samples / args.chunk_samples)
    for _ in range(silence_chunks):
        if state.first_audio_seen.is_set():
            break
        await websocket.send(pack_float32(silence_chunk))
        await asyncio.sleep(args.chunk_samples / sample_rate)


async def wait_for_turn_completion(state: TurnState, timeout_sec: float) -> None:
    await asyncio.wait_for(state.first_audio_seen.wait(), timeout=timeout_sec)
    try:
        await asyncio.wait_for(state.prompt_done.wait(), timeout=5.0)
    except TimeoutError:
        pass


def finalize_turn(state: TurnState) -> None:
    def delta_ms(value: float | None) -> float | None:
        if value is None or state.voice_end_at is None:
            return None
        return (value - state.voice_end_at) * 1000.0

    state.result.voice_end_to_transcript_ms = delta_ms(state.first_transcript_at)
    state.result.voice_end_to_tts_result_ms = delta_ms(state.first_tts_result_at)
    state.result.voice_end_to_first_audio_ms = delta_ms(state.first_audio_at)


def average(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * q) - 1)
    return ordered[index]


async def async_main() -> None:
    args = parse_args()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    server = start_server_for_url(args.url) if args.start_server else None
    try:
        result = await run_smoke(args, output_dir, stamp)
        output_path = output_dir / f"five-turn-smoke-{stamp}.json"
        result.artifact_path = str(output_path)
        payload = asdict(result)
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        append_latency_log(output_path, result)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"wrote {output_path}")
    finally:
        stop_server(server)


def start_server_for_url(url: str) -> subprocess.Popen[str] | None:
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8000
    http_url = f"http://{host}:{port}/"
    if http_ready(http_url):
        return None
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "server.hot_path.app:app",
            "--host",
            host,
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        text=True,
    )
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if http_ready(http_url):
            return process
        if process.poll() is not None:
            raise RuntimeError(f"hot-path server exited early with {process.returncode}")
        time.sleep(0.2)
    raise RuntimeError(f"hot-path server did not become ready: {http_url}")


def stop_server(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def http_ready(url: str) -> bool:
    try:
        response = httpx.get(url, timeout=1.0)
    except httpx.HTTPError:
        return False
    return response.status_code < 500


def append_latency_log(output_path: Path, result: FiveTurnResult) -> None:
    average_ms = (
        f"{result.average_first_audio_ms:.1f}ms"
        if result.average_first_audio_ms is not None
        else "not measured"
    )
    p95_ms = (
        f"{result.p95_first_audio_ms:.1f}ms"
        if result.p95_first_audio_ms is not None
        else "not measured"
    )
    with Path("_docs/latency.md").open("a", encoding="utf-8") as handle:
        handle.write(
            "| 2026-06-18 | Tomoko v2 five-turn real runtime smoke | "
            "`5x say -> /ws -> Apple Speech -> dflash -> VOICEVOX` | "
            f"avg first audio {average_ms}; p95 {p95_ms} | "
            f"turns={len(result.turns)}, artifact `{output_path}`. |\n"
        )


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
