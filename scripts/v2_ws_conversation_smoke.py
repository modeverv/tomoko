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
from pathlib import Path
from typing import Any

import httpx
import websockets


async def run_smoke(*, port: int, fake_runtime: bool) -> dict[str, Any]:
    url = f"ws://127.0.0.1:{port}/ws"
    events: list[dict[str, Any]] = []
    audio_chunks = 0
    audio_bytes = 0
    async with websockets.connect(url, max_size=16 * 1024 * 1024) as websocket:
        ready = json.loads(await websocket.recv())
        events.append(ready)
        if fake_runtime:
            for payload in _fake_audio_payloads():
                await websocket.send(payload)
        else:
            await websocket.send(
                json.dumps(
                    {
                        "type": "prompt",
                        "text": "トモコ、短く返事して。",
                        "scope": "main",
                    },
                    ensure_ascii=False,
                )
            )
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            message = await asyncio.wait_for(websocket.recv(), timeout=deadline - time.monotonic())
            if isinstance(message, bytes):
                audio_chunks += 1
                audio_bytes += len(message)
                continue
            payload = json.loads(message)
            events.append(payload)
            if payload.get("type") == "prompt_complete":
                break

    return {
        "fake_runtime": fake_runtime,
        "events": [event.get("type") for event in events],
        "transcript": _first_event_text(events, "transcript"),
        "reply": _first_event_text(events, "model_complete"),
        "audio_chunks": audio_chunks,
        "audio_bytes": audio_bytes,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--fake-runtime", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--start-processes", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    port = args.port or _free_port()
    server: subprocess.Popen[str] | None = None
    tomoko: subprocess.Popen[str] | None = None
    try:
        if args.start_processes:
            env = os.environ.copy()
            if args.fake_runtime:
                env["TOMOKO_V2_FAKE_RUNTIME"] = "1"
            Path("logs").mkdir(exist_ok=True)
            tomoko = subprocess.Popen(
                [sys.executable, "-m", "server.runtime", "process", "tomoko"],
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
            _wait_http_ready(f"http://127.0.0.1:{port}/")
        result = asyncio.run(run_smoke(port=port, fake_runtime=args.fake_runtime))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        for process in (server, tomoko):
            if process is not None and process.poll() is None:
                process.send_signal(signal.SIGINT)
        for process in (server, tomoko):
            if process is not None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()


def _fake_audio_payloads() -> list[bytes]:
    chunks: list[bytes] = []
    chunks.extend(_float32_bytes([0.002] * 512) for _ in range(5))
    chunks.extend(_float32_bytes([0.2] * 512) for _ in range(12))
    chunks.extend(_float32_bytes([0.0] * 512) for _ in range(20))
    return chunks


def _float32_bytes(samples: list[float]) -> bytes:
    values = array.array("f", samples)
    return values.tobytes()


def _first_event_text(events: list[dict[str, Any]], event_type: str) -> str:
    return str(next((event.get("text") for event in events if event.get("type") == event_type), ""))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_http_ready(url: str) -> None:
    deadline = time.monotonic() + 10.0
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


if __name__ == "__main__":
    main()
