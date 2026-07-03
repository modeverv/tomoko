from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

REAL_SCENARIOS = ("real-overlap-replace", "real-overlap-stop", "calendar-append")


@dataclass(slots=True)
class CommandSpec:
    argv: list[str]


@dataclass(slots=True)
class CommandPlan:
    commands: list[CommandSpec]
    skipped: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CommandResult:
    argv: list[str]
    returncode: int
    elapsed_ms: float


def build_command_plan(
    *,
    runtime_ready: bool,
    latency_count: int,
    latency_repeats: int,
    url: str | None = None,
    voice: str | None = None,
) -> CommandPlan:
    commands = [
        CommandSpec(["make", "check"]),
        CommandSpec(["make", "test-integration"]),
        CommandSpec(["make", "v2-scenario-suite"]),
    ]
    skipped: list[str] = []
    if not runtime_ready:
        skipped.extend([*REAL_SCENARIOS, "v2-latency-suite"])
        return CommandPlan(commands=commands, skipped=skipped)

    make_vars = _make_vars(url=url, voice=voice)
    for scenario in REAL_SCENARIOS:
        commands.append(
            CommandSpec(
                [
                    "make",
                    "v2-scenario-replay",
                    f"SCENARIO={scenario}",
                    "SCENARIO_RUNTIME=real",
                    *make_vars,
                ]
            )
        )
    commands.append(
        CommandSpec(
            [
                "make",
                "v2-latency-suite",
                f"LATENCY_SUITE_COUNT={latency_count}",
                f"LATENCY_SUITE_REPEATS={latency_repeats}",
                *make_vars,
            ]
        )
    )
    return CommandPlan(commands=commands)


def runtime_http_ready(ws_url: str) -> bool:
    try:
        response = httpx.get(_http_url_from_ws_url(ws_url), timeout=1.5)
    except httpx.HTTPError:
        return False
    return response.status_code < 500


def run_plan(plan: CommandPlan) -> list[CommandResult]:
    results: list[CommandResult] = []
    for command in plan.commands:
        start = time.monotonic()
        completed = subprocess.run(command.argv, check=False)
        elapsed_ms = (time.monotonic() - start) * 1000
        results.append(
            CommandResult(
                argv=command.argv,
                returncode=completed.returncode,
                elapsed_ms=round(elapsed_ms, 3),
            )
        )
        if completed.returncode != 0:
            break
    return results


def write_artifact(
    *,
    runtime_ready: bool,
    plan: CommandPlan,
    results: list[CommandResult],
    output_dir: Path,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = output_dir / f"autopilot-{stamp}.json"
    payload = {
        "runtime_ready": runtime_ready,
        "skipped": plan.skipped,
        "commands": [
            {
                "argv": result.argv,
                "returncode": result.returncode,
                "elapsed_ms": result.elapsed_ms,
            }
            for result in results
        ],
        "passed": all(result.returncode == 0 for result in results),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _make_vars(*, url: str | None, voice: str | None) -> list[str]:
    vars_: list[str] = []
    if url:
        vars_.append(f"WS_LATENCY_URL={url}")
    if voice:
        vars_.append(f"WS_LATENCY_VOICE={voice}")
    return vars_


def _http_url_from_ws_url(ws_url: str) -> str:
    parsed = urlparse(ws_url)
    host = parsed.hostname or "127.0.0.1"
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    port = parsed.port or 8000
    return f"http://{host}:{port}/"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://0.0.0.0:8000/ws")
    parser.add_argument("--voice", default="Kyoko")
    parser.add_argument("--latency-count", type=int, default=10)
    parser.add_argument("--latency-repeats", type=int, default=3)
    parser.add_argument("--output-dir", default="logs")
    args = parser.parse_args()

    ready = runtime_http_ready(args.url)
    plan = build_command_plan(
        runtime_ready=ready,
        latency_count=args.latency_count,
        latency_repeats=args.latency_repeats,
        url=args.url,
        voice=args.voice,
    )
    results = run_plan(plan)
    artifact = write_artifact(
        runtime_ready=ready,
        plan=plan,
        results=results,
        output_dir=Path(args.output_dir),
    )
    print(f"[autopilot] artifact={artifact}")
    if any(result.returncode != 0 for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
