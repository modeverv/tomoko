from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg

from server.shared.config import NodeConfig
from server.tools.initiative_motivation_sandbox import (
    load_candidate_export,
    load_jsonl,
    simulate_from_logs,
    simulate_recent_sessions_from_logs,
    simulate_silence,
    write_html,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/central_realtime.toml")
    parser.add_argument("--main", default="logs/turn-taking-main.jsonl")
    parser.add_argument("--v2", default="logs/turn-taking-v2-shadow.jsonl")
    parser.add_argument("--candidates", type=str)
    parser.add_argument("--output", required=True)
    parser.add_argument("--html", type=str)
    parser.add_argument("--mode", choices=("logs", "silence"), default="logs")
    parser.add_argument("--duration-sec", type=int, default=300)
    parser.add_argument(
        "--window-sec",
        type=int,
        default=1800,
        help="For log mode, use only the latest N seconds unless --all-logs is set.",
    )
    parser.add_argument("--all-logs", action="store_true")
    parser.add_argument(
        "--recent-sessions",
        type=int,
        default=0,
        help="Build a selectable multi-session UI from the latest N session IDs.",
    )
    parser.add_argument(
        "--recent-session-source",
        choices=("db", "jsonl"),
        default="db",
        help="For --recent-sessions, choose conversation_logs from DB or JSONL records.",
    )
    parser.add_argument("--step-sec", type=float, default=1.0)
    parser.add_argument("--threshold", type=float, default=0.65)
    parser.add_argument("--curiosity-gain", type=float)
    parser.add_argument("--teasing-gain", type=float)
    parser.add_argument("--attachment-gain", type=float)
    parser.add_argument("--unspoken-gain", type=float)
    return parser.parse_args()


def params_from_args(args: argparse.Namespace) -> dict[str, float]:
    pairs = {
        "threshold": args.threshold,
        "curiosity_gain": args.curiosity_gain,
        "teasing_gain": args.teasing_gain,
        "attachment_gain": args.attachment_gain,
        "unspoken_gain": args.unspoken_gain,
    }
    return {key: float(value) for key, value in pairs.items() if value is not None}


def filter_latest_window(
    main_records: list[dict],
    v2_records: list[dict],
    *,
    window_sec: int,
) -> tuple[list[dict], list[dict]]:
    all_ts = [
        int(record.get("ts_ms") or 0)
        for record in [*main_records, *v2_records]
        if int(record.get("ts_ms") or 0) > 0
    ]
    if not all_ts:
        return main_records, v2_records
    latest = max(all_ts)
    start = latest - max(1, window_sec) * 1000
    return (
        [record for record in main_records if int(record.get("ts_ms") or 0) >= start],
        [record for record in v2_records if int(record.get("ts_ms") or 0) >= start],
    )


def fetch_recent_conversation_records(*, config_path: str, limit: int) -> list[dict[str, Any]]:
    config = NodeConfig.load(config_path)
    session_limit = max(1, limit)
    query = """
    WITH recent_sessions AS (
        SELECT conversation_session_id, max(recorded_at) AS last_recorded_at
        FROM conversation_logs
        WHERE status = 'completed'
          AND conversation_session_id IS NOT NULL
          AND role IN ('user', 'tomoko')
        GROUP BY conversation_session_id
        ORDER BY last_recorded_at DESC
        LIMIT %s
    )
    SELECT
        l.conversation_session_id,
        l.recorded_at,
        l.role,
        l.speaker,
        l.transcript,
        l.emotion,
        l.participation_mode,
        l.status
    FROM conversation_logs l
    JOIN recent_sessions s ON s.conversation_session_id = l.conversation_session_id
    WHERE l.status = 'completed'
      AND l.role IN ('user', 'tomoko')
    ORDER BY s.last_recorded_at DESC, l.recorded_at ASC
    """
    with psycopg.connect(config.database.dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (session_limit,))
            rows = cur.fetchall()
    return [_conversation_row_to_record(row) for row in rows]


def _conversation_row_to_record(row: tuple[Any, ...]) -> dict[str, Any]:
    (
        session_id,
        recorded_at,
        role,
        speaker,
        transcript,
        emotion,
        participation_mode,
        status,
    ) = row
    event = "final_transcript_received" if role == "user" else "tomoko_turn_completed"
    return {
        "ts_ms": _datetime_to_ms(recorded_at),
        "conversation_session_id": str(session_id),
        "lane": "main",
        "event": event,
        "role": str(role),
        "speaker": speaker,
        "text": str(transcript or ""),
        "emotion": emotion,
        "participation_mode": participation_mode,
        "status": status,
        "source": "conversation_logs",
    }


def _datetime_to_ms(value: Any) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    return int(datetime.fromisoformat(str(value)).timestamp() * 1000)


def main() -> None:
    args = parse_args()
    candidates = load_candidate_export(Path(args.candidates) if args.candidates else None)
    params = params_from_args(args)
    if args.mode == "silence":
        simulation = simulate_silence(
            candidates=candidates,
            duration_sec=args.duration_sec,
            params=params,
            step_sec=args.step_sec,
        )
    else:
        main_records = load_jsonl(Path(args.main))
        v2_records = load_jsonl(Path(args.v2))
        if args.recent_sessions > 0:
            if args.recent_session_source == "db":
                main_records = fetch_recent_conversation_records(
                    config_path=args.config,
                    limit=args.recent_sessions,
                )
            simulation = simulate_recent_sessions_from_logs(
                main_records=main_records,
                v2_records=v2_records,
                candidates=candidates,
                limit=args.recent_sessions,
                params=params,
                step_sec=args.step_sec,
            )
        else:
            if not args.all_logs:
                main_records, v2_records = filter_latest_window(
                    main_records,
                    v2_records,
                    window_sec=args.window_sec,
                )
            simulation = simulate_from_logs(
                main_records=main_records,
                v2_records=v2_records,
                candidates=candidates,
                params=params,
                step_sec=args.step_sec,
            )

    output = Path(args.output)
    write_json(output, simulation)
    html_path = Path(args.html) if args.html else output.with_suffix(".html")
    write_html(html_path, simulation)
    print(
        json.dumps(
            {
                "output": str(output),
                "html": str(html_path),
                "summary": simulation["summary"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
