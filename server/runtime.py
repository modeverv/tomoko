from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx
import psycopg

from server.audio.stt import apple_speech_runtime_available
from server.info.main import calendar_dto_map, parse_minimal_ics
from server.shared.logging import JsonlLogger
from server.shared.models import new_id
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


async def run_process(process_name: str) -> None:
    logger = JsonlLogger(Path("logs/v2-runtime.jsonl"))
    logger.log("process_start", process=process_name, readiness=readiness_snapshot())
    try:
        while True:
            logger.log("heartbeat", process=process_name)
            await asyncio.sleep(5)
    except asyncio.CancelledError:
        logger.log("process_stop", process=process_name)
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
    dsn = os.environ.get("TOMOKO_DATABASE_URL")
    if not dsn:
        return False
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


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    process = subparsers.add_parser("process")
    process.add_argument("name")
    subparsers.add_parser("info-once")
    subparsers.add_parser("readiness")
    subparsers.add_parser("report-latest")
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
    elif args.command == "info-once":
        print(json.dumps(info_once(), ensure_ascii=False))
    elif args.command == "readiness":
        print(json.dumps(readiness_snapshot(), ensure_ascii=False))
    elif args.command == "report-latest":
        print(report_latest())


if __name__ == "__main__":
    main()
