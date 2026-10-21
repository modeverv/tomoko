from __future__ import annotations

import argparse
import array
import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg
import websockets

from server.shared.db import default_dsn


@dataclass(slots=True)
class DbSplitSmokeResult:
    fake_runtime: bool
    dsn: str
    port: int
    transcript: str | None
    speech_order_text: str | None
    speech_order_mode: str | None
    audio_chunks: int
    audio_bytes: int
    ready_to_transcript_ms: float | None
    transcript_to_order_ms: float | None
    order_to_first_audio_ms: float | None
    total_ms: float
    db_counts: dict[str, int]
    event_types: list[str] = field(default_factory=list)
    artifact_path: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default=default_dsn())
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--fake-runtime", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--start-db", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--start-processes", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--timeout-sec", type=float, default=30.0)
    parser.add_argument("--output-dir", default="logs")
    return parser.parse_args()


async def run_ws_smoke(
    port: int,
    timeout_sec: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    url = f"ws://127.0.0.1:{port}/ws"
    events: list[dict[str, Any]] = []
    audio_chunks = 0
    audio_bytes = 0
    first_audio_at: float | None = None
    started = time.perf_counter()
    async with websockets.connect(url, max_size=16 * 1024 * 1024) as websocket:
        ready = json.loads(await websocket.recv())
        ready["elapsed_ms"] = (time.perf_counter() - started) * 1000.0
        events.append(ready)
        for payload in fake_audio_payloads():
            await websocket.send(payload)
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            message = await asyncio.wait_for(websocket.recv(), timeout=deadline - time.monotonic())
            now = time.perf_counter()
            if isinstance(message, bytes):
                audio_chunks += 1
                audio_bytes += len(message)
                if first_audio_at is None:
                    first_audio_at = now
                continue
            payload = json.loads(message)
            payload["elapsed_ms"] = (now - started) * 1000.0
            events.append(payload)
            if payload.get("type") == "prompt_complete":
                break
    summary = {
        "audio_chunks": audio_chunks,
        "audio_bytes": audio_bytes,
        "first_audio_elapsed_ms": (first_audio_at - started) * 1000.0
        if first_audio_at is not None
        else None,
        "total_ms": (time.perf_counter() - started) * 1000.0,
    }
    return summary, events


def main() -> None:
    args = parse_args()
    port = args.port or free_port()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / f"db-split-smoke-{stamp}.json"
    tomoko: subprocess.Popen[str] | None = None
    server: subprocess.Popen[str] | None = None
    try:
        if args.start_db:
            subprocess.run(["make", "db-up"], check=True, text=True)
        wait_database(args.dsn)
        apply_schema(args.dsn)
        if args.start_processes:
            env = os.environ.copy()
            env["TOMOKO_DATABASE_URL"] = args.dsn
            env["TOMOKO_V2_DB_SPLIT"] = "1"
            if args.fake_runtime:
                env["TOMOKO_V2_FAKE_RUNTIME"] = "1"
                env["TOMOKO_V2_FAKE_REPLY"] = "うん、DB越しに聞こえてるよ。"
            tomoko = subprocess.Popen(
                [sys.executable, "-m", "server.runtime", "process", "tomoko-db"],
                env=env,
                text=True,
            )
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "server.hot_path.app:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--log-level",
                    "warning",
                ],
                env=env,
                text=True,
            )
            wait_http_ready(f"http://127.0.0.1:{port}/")
            time.sleep(0.5)
        summary, events = asyncio.run(run_ws_smoke(port, args.timeout_sec))
        result = build_result(
            args=args,
            port=port,
            events=events,
            summary=summary,
            artifact_path=artifact_path,
        )
        artifact_path.write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        append_latency_log(result)
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    finally:
        stop_process(server)
        stop_process(tomoko)


def build_result(
    *,
    args: argparse.Namespace,
    port: int,
    events: list[dict[str, Any]],
    summary: dict[str, Any],
    artifact_path: Path,
) -> DbSplitSmokeResult:
    transcript_event = first_event(events, "transcript")
    order_event = first_event(events, "speech_order")
    ready_event = first_event(events, "ready")
    transcript_ms = elapsed(transcript_event)
    order_ms = elapsed(order_event)
    first_audio_ms = summary.get("first_audio_elapsed_ms")
    return DbSplitSmokeResult(
        fake_runtime=args.fake_runtime,
        dsn=args.dsn,
        port=port,
        transcript=str(transcript_event.get("text")) if transcript_event else None,
        speech_order_text=str(order_event.get("text")) if order_event else None,
        speech_order_mode=str(order_event.get("mode")) if order_event else None,
        audio_chunks=int(summary["audio_chunks"]),
        audio_bytes=int(summary["audio_bytes"]),
        ready_to_transcript_ms=delta(elapsed(ready_event), transcript_ms),
        transcript_to_order_ms=delta(transcript_ms, order_ms),
        order_to_first_audio_ms=delta(order_ms, first_audio_ms),
        total_ms=float(summary["total_ms"]),
        db_counts=query_db_counts(args.dsn),
        event_types=[str(event.get("type")) for event in events],
        artifact_path=str(artifact_path),
    )


def apply_schema(dsn: str) -> None:
    ddl = Path("docker/postgres/init/100_v2_core.sql").read_text(encoding="utf-8")
    with psycopg.connect(dsn) as conn:
        conn.execute(ddl)


def wait_database(dsn: str) -> None:
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(dsn, connect_timeout=2) as conn:
                conn.execute("SELECT 1")
                return
        except psycopg.Error:
            time.sleep(0.5)
    raise RuntimeError("database did not become ready")


def wait_http_ready(url: str) -> None:
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=1.0)
        except httpx.HTTPError:
            time.sleep(0.2)
            continue
        if response.status_code < 500:
            return
        time.sleep(0.2)
    raise RuntimeError(f"server did not become ready: {url}")


def query_db_counts(dsn: str) -> dict[str, int]:
    tables = [
        "v2_stt_observations",
        "v2_semantic_saturation_observations",
        "v2_speech_scheduler_decisions",
        "v2_speech_orders",
        "v2_audio_output_events",
    ]
    counts: dict[str, int] = {}
    with psycopg.connect(dsn) as conn:
        for table in tables:
            counts[table] = int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
    return counts


def append_latency_log(result: DbSplitSmokeResult) -> None:
    with Path("_docs/latency.md").open("a", encoding="utf-8") as handle:
        handle.write(
            "| 2026-06-18 | Tomoko v2 DB split fake LISTEN/NOTIFY smoke | "
            "`hot-path STT -> DB/NOTIFY -> tomoko-process -> DB/NOTIFY -> hot-path TTS` | "
            f"total {result.total_ms:.1f}ms | "
            f"transcript->order {result.transcript_to_order_ms:.1f}ms, "
            f"order->first audio {result.order_to_first_audio_ms:.1f}ms, "
            f"artifact `{result.artifact_path}`. |\n"
        )


def fake_audio_payloads() -> list[bytes]:
    chunks: list[bytes] = []
    chunks.extend(float32_bytes([0.002] * 512) for _ in range(5))
    chunks.extend(float32_bytes([0.2] * 512) for _ in range(12))
    chunks.extend(float32_bytes([0.0] * 512) for _ in range(20))
    return chunks


def float32_bytes(samples: list[float]) -> bytes:
    values = array.array("f", samples)
    return values.tobytes()


def first_event(events: list[dict[str, Any]], event_type: str) -> dict[str, Any] | None:
    return next((event for event in events if event.get("type") == event_type), None)


def elapsed(event: dict[str, Any] | None) -> float | None:
    if event is None:
        return None
    value = event.get("elapsed_ms")
    return float(value) if isinstance(value, int | float) else None


def delta(start: float | None, end: float | None) -> float | None:
    if start is None or end is None:
        return None
    return end - start


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def stop_process(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


if __name__ == "__main__":
    main()
