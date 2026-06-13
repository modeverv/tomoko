from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from server.shared.config import NodeConfig
from server.shared.inference.router import InferenceRouter
from server.tools.initiative_motivation_sandbox import write_json

SYSTEM_PROMPT = """You are Tomoko.
You are generating an offline dry-run for a possible spontaneous spoken line.
Return Japanese only. Keep it short. Do not mention that this is a simulation.
Start with EMOTION:<neutral|happy|thinking|surprised|sad|angry>, then one short line."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulation", required=True)
    parser.add_argument("--marker-id", required=True)
    parser.add_argument("--config", default="config/central_realtime.toml")
    parser.add_argument("--role", default="conversation")
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


async def run_dryrun(
    *,
    simulation_path: Path,
    marker_id: str,
    config_path: Path,
    role: str,
    max_tokens: int,
) -> dict[str, Any]:
    simulation = json.loads(simulation_path.read_text(encoding="utf-8"))
    marker, session_id = find_marker(simulation, marker_id)
    if marker is None:
        raise ValueError(f"marker not found: {marker_id}")
    prompt = str(marker.get("prompt_preview") or "")
    config = NodeConfig.load(config_path)
    router = InferenceRouter(config=config)
    backend = await router.select(role, "privacy")
    started = time.perf_counter()
    first_delta_ms: float | None = None
    chunks: list[str] = []
    error: str | None = None
    try:
        async for chunk in backend.chat_stream(
            SYSTEM_PROMPT,
            [{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            trace_role="initiative_prompt_dryrun",
        ):
            if first_delta_ms is None:
                first_delta_ms = elapsed_ms(started)
            chunks.append(chunk)
    except Exception as exc:  # noqa: BLE001 - dry-run should capture backend errors
        error = f"{type(exc).__name__}: {exc}"
    total_ms = elapsed_ms(started)
    raw_text = "".join(chunks).strip()
    return {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "simulation": str(simulation_path),
        "marker_id": marker_id,
        "session_id": session_id,
        "role": role,
        "backend": getattr(backend, "name", "unknown"),
        "max_tokens": max_tokens,
        "prompt": prompt,
        "raw_text": raw_text,
        "format": inspect_output(raw_text),
        "first_delta_ms": first_delta_ms,
        "total_ms": total_ms,
        "error": error,
        "marker": marker,
    }


def find_marker(
    simulation: dict[str, Any],
    marker_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    for item in simulation.get("fire_markers", []):
        if item.get("id") == marker_id:
            return item, None
    for session in simulation.get("sessions", []):
        inner = session.get("simulation", {})
        for item in inner.get("fire_markers", []):
            if item.get("id") == marker_id:
                return item, str(session.get("session_id"))
    return None, None


def inspect_output(raw_text: str) -> dict[str, Any]:
    first_line = raw_text.lstrip().splitlines()[0].strip() if raw_text.strip() else ""
    body = "\n".join(raw_text.lstrip().splitlines()[1:]).strip()
    starts_with_emotion = first_line.startswith("EMOTION:")
    return {
        "first_line": first_line,
        "starts_with_emotion": starts_with_emotion,
        "body": body if starts_with_emotion else raw_text.strip(),
        "body_char_count": len(body if starts_with_emotion else raw_text.strip()),
    }


def elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


async def _main() -> None:
    args = parse_args()
    result = await run_dryrun(
        simulation_path=Path(args.simulation),
        marker_id=args.marker_id,
        config_path=Path(args.config),
        role=args.role,
        max_tokens=args.max_tokens,
    )
    output = Path(args.output)
    write_json(output, result)
    print(
        json.dumps(
            {
                "output": str(output),
                "backend": result["backend"],
                "first_delta_ms": result["first_delta_ms"],
                "total_ms": result["total_ms"],
                "error": result["error"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(_main())
