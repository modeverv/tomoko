from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from server.info.main import calendar_dto_map, parse_minimal_ics
from server.shared.logging import JsonlLogger
from server.shared.models import new_id


def readiness_snapshot() -> dict[str, object]:
    return {
        "database_url_env": "TOMOKO_DATABASE_URL",
        "llm_runtime": "configured externally",
        "voicevox": "configured externally",
        "apple_speech": "macOS runtime required",
        "ocr": "optional",
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
        asyncio.run(run_process(args.name))
    elif args.command == "info-once":
        print(json.dumps(info_once(), ensure_ascii=False))
    elif args.command == "readiness":
        print(json.dumps(readiness_snapshot(), ensure_ascii=False))
    elif args.command == "report-latest":
        print(report_latest())


if __name__ == "__main__":
    main()
