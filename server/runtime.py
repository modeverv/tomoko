from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row

from server.audio.stt import apple_speech_runtime_available
from server.info.main import (
    calendar_dto_map,
    materialize_info_fixtures_from_env,
    parse_minimal_ics,
)
from server.runtime_ports import check_tcp_port_available
from server.shared.db import default_dsn
from server.shared.logging import JsonlLogger
from server.shared.models import new_id
from server.summary.main import materialize_summaries_from_db
from server.think.main import materialize_candidates_from_db
from server.tomoko.db_worker import run_default_worker
from server.user_status.ocr_runtime import ocr_runtime_available


def readiness_snapshot() -> dict[str, object]:
    llm_urls = os.environ.get(
        "TOMOKO_V2_LLM_READY_URLS",
        "http://127.0.0.1:8081/v1/models http://127.0.0.1:8082/v1/models",
    ).split()
    voicevox_url = os.environ.get(
        "TOMOKO_V2_VOICEVOX_READY_URL",
        "http://127.0.0.1:50122/version",
    )
    return {
        "database": _database_ready(),
        "llm": {url: _http_ready(url) for url in llm_urls},
        "voicevox": {voicevox_url: _http_ready(voicevox_url)},
        "apple_speech": apple_speech_runtime_available(),
        "ocr": ocr_runtime_available(),
    }


def readiness_transition_events(
    previous: dict[str, object] | None,
    current: dict[str, object],
) -> list[dict[str, object]]:
    current_flat = _flatten_readiness(current)
    if previous is None:
        return [
            {
                "event": "readiness_snapshot",
                "component": path.split(".", 1)[0],
                "ready": ready,
                "path": path,
            }
            for path, ready in current_flat.items()
        ]
    previous_flat = _flatten_readiness(previous)
    events: list[dict[str, object]] = []
    for path, ready in current_flat.items():
        previous_ready = previous_flat.get(path)
        if previous_ready is None or previous_ready == ready:
            continue
        events.append(
            {
                "event": "readiness_transition",
                "component": path.split(".", 1)[0],
                "previous_ready": previous_ready,
                "ready": ready,
                "path": path,
            }
        )
    return events


async def run_process(process_name: str) -> None:
    if process_name in {"tomoko-db", "tomoko-split"}:
        fake_reply = os.environ.get("TOMOKO_V2_FAKE_REPLY")
        await run_default_worker(
            os.environ.get("TOMOKO_DATABASE_URL", default_dsn()),
            fake_reply=fake_reply if os.environ.get("TOMOKO_V2_FAKE_RUNTIME") == "1" else None,
        )
        return

    logger = JsonlLogger(Path("logs/v2-runtime.jsonl"))
    readiness = readiness_snapshot()
    logger.log("process_start", process=process_name, readiness=readiness)
    for event in readiness_transition_events(None, readiness):
        logger.log(str(event["event"]), process=process_name, **_event_fields(event))
        _console_event(process_name, str(event["event"]), **_event_fields(event))
    _console_event(
        process_name,
        "process_start",
        readiness=json.dumps(readiness, ensure_ascii=False),
    )
    try:
        previous_readiness = readiness
        while True:
            readiness = readiness_snapshot()
            for event in readiness_transition_events(previous_readiness, readiness):
                logger.log(str(event["event"]), process=process_name, **_event_fields(event))
                _console_event(process_name, str(event["event"]), **_event_fields(event))
            previous_readiness = readiness
            if process_name == "info":
                await _run_info_tick(logger)
            if process_name == "think":
                await _run_think_candidate_tick(logger)
            if process_name == "summary":
                await _run_summary_tick(logger)
            logger.log("heartbeat", process=process_name)
            _console_event(process_name, "heartbeat")
            await asyncio.sleep(5)
    except asyncio.CancelledError:
        logger.log("process_stop", process=process_name)
        _console_event(process_name, "process_stop", reason="cancelled")
        raise


def info_once() -> dict[str, object]:
    sample = """BEGIN:VEVENT
DTSTART:20260618T120000
SUMMARY:sample calendar import hook
END:VEVENT
"""
    events = parse_minimal_ics(sample)
    payload = {"events": calendar_dto_map(events)}
    JsonlLogger(Path("logs/v2-runtime.jsonl")).log("info_once", **payload)
    _console_event("runtime", "info_once", events=len(events))
    return payload


def report_latest() -> Path:
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    output = reports_dir / "v2-latest.html"
    log_path = Path("logs/v2-runtime.jsonl")
    rows = []
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines()[-100:]:
            rows.append(json.loads(line))
    html_rows = "\n".join(
        "<tr>"
        f"<td>{row.get('ts')}</td>"
        f"<td>{row.get('event')}</td>"
        f"<td><pre>{json.dumps(row, ensure_ascii=False)}</pre></td>"
        "</tr>"
        for row in rows
    )
    output.write_text(
        "<!doctype html><meta charset='utf-8'><title>Tomoko v2 latest</title>"
        "<h1>Tomoko v2 latest timeline</h1>"
        f"<p>report_id={new_id()}</p>"
        f"<table>{html_rows}</table>",
        encoding="utf-8",
    )
    return output


def _database_ready() -> bool:
    dsn = os.environ.get("TOMOKO_DATABASE_URL", default_dsn())
    try:
        with psycopg.connect(dsn, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
    except psycopg.Error:
        return False
    return True


def _http_ready(url: str) -> bool:
    try:
        response = httpx.get(url, timeout=2.0)
    except httpx.HTTPError:
        return False
    return response.status_code < 500


async def _run_think_candidate_tick(logger: JsonlLogger) -> None:
    try:
        async with await psycopg.AsyncConnection.connect(
            os.environ.get("TOMOKO_DATABASE_URL", default_dsn()),
            autocommit=True,
            row_factory=dict_row,
        ) as conn:
            result = await materialize_candidates_from_db(conn)
    except Exception as exc:
        logger.log("think_candidate_tick_failed", process="think", error=type(exc).__name__)
        _console_event("think", "candidate_tick_failed", error=type(exc).__name__)
        return
    logger.log(
        "think_candidates_materialized",
        process="think",
        summaries_read=result.summaries_read,
        world_items_read=result.world_items_read,
        candidates_upserted=result.candidates_upserted,
    )
    _console_event(
        "think",
        "candidates_materialized",
        summaries_read=result.summaries_read,
        world_items_read=result.world_items_read,
        candidates_upserted=result.candidates_upserted,
    )


async def _run_summary_tick(logger: JsonlLogger) -> None:
    try:
        async with await psycopg.AsyncConnection.connect(
            os.environ.get("TOMOKO_DATABASE_URL", default_dsn()),
            autocommit=True,
            row_factory=dict_row,
        ) as conn:
            result = await materialize_summaries_from_db(conn)
    except Exception as exc:
        logger.log("summary_tick_failed", process="summary", error=type(exc).__name__)
        _console_event("summary", "summary_tick_failed", error=type(exc).__name__)
        return
    logger.log(
        "summaries_materialized",
        process="summary",
        sessions_read=result.sessions_read,
        summaries_upserted=result.summaries_upserted,
    )
    _console_event(
        "summary",
        "summaries_materialized",
        sessions_read=result.sessions_read,
        summaries_upserted=result.summaries_upserted,
    )


async def _run_info_tick(logger: JsonlLogger) -> None:
    try:
        async with await psycopg.AsyncConnection.connect(
            os.environ.get("TOMOKO_DATABASE_URL", default_dsn()),
            autocommit=True,
            row_factory=dict_row,
        ) as conn:
            result = await materialize_info_fixtures_from_env(conn)
    except Exception as exc:
        logger.log("info_tick_failed", process="info", error=type(exc).__name__)
        _console_event("info", "info_tick_failed", error=type(exc).__name__)
        return
    logger.log(
        "info_fixtures_materialized",
        process="info",
        documents_upserted=result.documents_upserted,
        items_upserted=result.items_upserted,
        interpretations_upserted=result.interpretations_upserted,
    )
    _console_event(
        "info",
        "fixtures_materialized",
        documents_upserted=result.documents_upserted,
        items_upserted=result.items_upserted,
        interpretations_upserted=result.interpretations_upserted,
    )


def _flatten_readiness(payload: dict[str, object], prefix: str = "") -> dict[str, bool]:
    flattened: dict[str, bool] = {}
    for key, value in payload.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, bool):
            flattened[path] = value
        elif isinstance(value, dict):
            flattened.update(_flatten_readiness(dict(value), path))
    return flattened


def _event_fields(event: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in event.items() if key != "event"}


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    process = subparsers.add_parser("process")
    process.add_argument("name")
    subparsers.add_parser("info-once")
    subparsers.add_parser("readiness")
    subparsers.add_parser("report-latest")
    port_guard = subparsers.add_parser("guard-internal-ws-port")
    port_guard.add_argument(
        "--host",
        default=os.environ.get("TOMOKO_INTERNAL_WS_BIND_HOST", "0.0.0.0"),
    )
    port_guard.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("TOMOKO_INTERNAL_WS_PORT", "8765")),
    )
    args = parser.parse_args()
    if args.command == "process":
        try:
            asyncio.run(run_process(args.name))
        except KeyboardInterrupt:
            JsonlLogger(Path("logs/v2-runtime.jsonl")).log(
                "process_stop",
                process=args.name,
                reason="keyboard_interrupt",
            )
            _console_event(args.name, "process_stop", reason="keyboard_interrupt")
    elif args.command == "info-once":
        print(json.dumps(info_once(), ensure_ascii=False))
    elif args.command == "readiness":
        snapshot = readiness_snapshot()
        _console_event("runtime", "readiness", readiness=json.dumps(snapshot, ensure_ascii=False))
        print(json.dumps(snapshot, ensure_ascii=False))
    elif args.command == "report-latest":
        print(report_latest())
    elif args.command == "guard-internal-ws-port":
        result = check_tcp_port_available(args.host, args.port)
        if not result.available:
            print(result.message())
            raise SystemExit(2)
        _console_event(
            "runtime",
            "internal_ws_port_available",
            host=args.host,
            port=args.port,
        )


def _console_event(process: str, event: str, **fields: object) -> None:
    parts = [f"[tomoko:{process}] {event}"]
    for key, value in fields.items():
        text = str(value)
        if len(text) > 180:
            text = text[:177] + "..."
        parts.append(f"{key}={text!r}")
    print(" ".join(parts), flush=True)


if __name__ == "__main__":
    main()
