from __future__ import annotations

import argparse
import asyncio
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.edge.pipeline.stt_apple import AppleSpeechSTT  # noqa: E402
from server.shared.config import BackendSpec, NodeConfig  # noqa: E402


@dataclass(frozen=True)
class PrepareResult:
    name: str
    status: str
    detail: str


def prepare_runtime(
    *,
    config_path: str | Path,
    launch_apps: bool = True,
    voicevox_wait_s: float = 20.0,
) -> list[PrepareResult]:
    config = NodeConfig.load(config_path)
    results: list[PrepareResult] = []

    tts_backend = config.backends[config.inference.tts_backend]
    results.append(
        prepare_tts_backend(
            tts_backend,
            launch_apps=launch_apps,
            wait_s=voicevox_wait_s,
        )
    )

    if config.inference.stt_backend is not None:
        stt_backend = config.backends[config.inference.stt_backend]
        results.append(prepare_stt_backend(stt_backend))
    else:
        results.append(PrepareResult("stt", "skip", "stt_backend is not configured"))

    return results


def prepare_tts_backend(
    spec: BackendSpec,
    *,
    launch_apps: bool = True,
    wait_s: float = 20.0,
) -> PrepareResult:
    if spec.type not in {"voicevox", "voicevox_stream", "voicevox_chunked"}:
        return PrepareResult("tts", "skip", f"{spec.name} type={spec.type} needs no app prepare")
    if not spec.url:
        return PrepareResult("tts", "error", f"{spec.name} has no url")

    if is_voicevox_ready(spec.url):
        return PrepareResult("tts", "ready", f"VOICEVOX Engine is already responding at {spec.url}")

    if not launch_apps:
        return PrepareResult("tts", "error", f"VOICEVOX Engine is not responding at {spec.url}")

    try:
        launch_voicevox_app()
    except Exception as exc:  # noqa: BLE001
        return PrepareResult("tts", "error", f"failed to open VOICEVOX.app: {exc}")
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if is_voicevox_ready(spec.url):
            return PrepareResult("tts", "started", f"VOICEVOX Engine started at {spec.url}")
        time.sleep(0.5)

    return PrepareResult(
        "tts",
        "error",
        f"VOICEVOX.app was opened but Engine did not respond at {spec.url}",
    )


def prepare_stt_backend(spec: BackendSpec) -> PrepareResult:
    if spec.type == "apple_speech":
        transcriber = AppleSpeechSTT(
            command=spec.command,
            language=spec.language or "ja-JP",
            on_device=spec.on_device,
            timeout_s=spec.timeout_s or 30.0,
        )
        asyncio.run(transcriber.warm_up())
        return PrepareResult("stt", "ready", "Apple Speech sidecar is built")

    return PrepareResult("stt", "skip", f"{spec.name} type={spec.type} needs no build prepare")


def is_voicevox_ready(url: str) -> bool:
    endpoint = url.rstrip("/") + "/version"
    request = urllib.request.Request(endpoint, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=1.0) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError):
        return False


def launch_voicevox_app() -> None:
    if platform.system() != "Darwin":
        raise RuntimeError("VOICEVOX auto launch is only supported on macOS")
    subprocess.run(
        ["open", "-a", "VOICEVOX"],
        check=True,
        capture_output=True,
        text=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default="config/central_realtime.toml",
        help="Tomoko runtime config to prepare",
    )
    parser.add_argument(
        "--no-launch-apps",
        action="store_true",
        help="check app-backed services without launching missing apps",
    )
    parser.add_argument(
        "--voicevox-wait-s",
        type=float,
        default=20.0,
        help="seconds to wait after opening VOICEVOX.app",
    )
    args = parser.parse_args()

    results = prepare_runtime(
        config_path=args.config,
        launch_apps=not args.no_launch_apps,
        voicevox_wait_s=args.voicevox_wait_s,
    )
    failed = False
    for result in results:
        print(f"{result.name}: {result.status}: {result.detail}", flush=True)
        failed = failed or result.status == "error"
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
