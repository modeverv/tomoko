from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from server.shared.models import new_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", default="logs/v2-runtime.jsonl")
    parser.add_argument("--output", default="reports/v2-scheduler-report.html")
    return parser.parse_args()


def load_scheduler_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if payload.get("event") in {
            "semantic_saturation",
            "speech_scheduler_decision",
            "scheduler_conversation_smoke",
        }:
            rows.append(payload)
    return rows


def build_report(rows: list[dict[str, Any]], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(
        "<tr>"
        f"<td>{row.get('ts', '')}</td>"
        f"<td>{row.get('event', '')}</td>"
        f"<td>{row.get('action', '')}</td>"
        f"<td>{row.get('score', '')}</td>"
        f"<td><pre>{json.dumps(row.get('score_breakdown', {}), ensure_ascii=False)}</pre></td>"
        f"<td><pre>{json.dumps(row, ensure_ascii=False)}</pre></td>"
        "</tr>"
        for row in rows[-200:]
    )
    output.write_text(
        "<!doctype html><meta charset='utf-8'>"
        "<title>Tomoko v2 scheduler report</title>"
        "<h1>Tomoko v2 scheduler report</h1>"
        f"<p>report_id={new_id()}</p>"
        "<p>Labels: false_interruption / too_quiet / too_chatty / missed_calendar_notice</p>"
        "<table>"
        "<thead><tr><th>ts</th><th>event</th><th>action</th><th>score</th>"
        "<th>score_breakdown</th><th>raw</th></tr></thead>"
        f"<tbody>{body}</tbody></table>",
        encoding="utf-8",
    )
    return output


def main() -> None:
    args = parse_args()
    output = build_report(load_scheduler_rows(Path(args.log)), Path(args.output))
    print(output)


if __name__ == "__main__":
    main()
