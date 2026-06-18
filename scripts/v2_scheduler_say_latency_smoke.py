from __future__ import annotations

import argparse
import asyncio
import json
import signal
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from scripts.v2_say_latency_smoke import generate_say_wav, measure


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run real say -> /ws scheduler conversation latency smoke."
    )
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws")
    parser.add_argument("--text", default="トモコ、短く返事して。")
    parser.add_argument("--voice", default="Kyoko")
    parser.add_argument("--chunk-samples", type=int, default=128)
    parser.add_argument("--trailing-silence-ms", type=int, default=2500)
    parser.add_argument("--timeout-sec", type=float, default=45.0)
    parser.add_argument("--output-dir", default="logs")
    parser.add_argument("--start-server", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


async def async_main() -> None:
    args = parse_args()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir)
    server = start_server_for_url(args.url) if args.start_server else None
    try:
        wav_path = generate_say_wav(args.text, args.voice, output_dir, stamp)
        result = await measure(args, wav_path)
        payload = asdict(result)
        output_path = output_dir / f"scheduler-say-latency-{stamp}.json"
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        append_latency_log(output_path, payload)
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


def append_latency_log(output_path: Path, payload: dict[str, object]) -> None:
    path = Path("_docs/latency.md")
    first_audio = payload.get("voice_end_to_first_audio_ms")
    result = f"{first_audio:.1f}ms" if isinstance(first_audio, float) else "not measured"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            "| 2026-06-18 | Tomoko v2 scheduler real say latency smoke | "
            "`say -> /ws -> Apple Speech -> scheduler -> dflash -> speech-order -> VOICEVOX` | "
            f"voice-end to first audio {result} | artifact `{output_path}`. |\n"
        )


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
