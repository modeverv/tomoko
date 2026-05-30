from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from _tools.analyze_server_debug_log import classify_event, read_tail_lines

CONTEXT_RE = re.compile(
    r"ContextSnapshotBuilder depth=(?P<depth>\w+) "
    r"elapsed_ms=(?P<elapsed>[0-9.]+) budget_ms=(?P<budget>[0-9.]+) "
    r"timed_out=(?P<timed_out>True|False)"
)
COUNT_RE = re.compile(
    r"\b(?P<key>recent_turns|session_summaries|memory_hits|lexicon_terms)="
    r"(?P<value>\d+)"
)
LOG_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[,.]\d{3}) "
    r"(?P<level>[A-Z]+):(?P<logger>[^:]+):(?P<message>.*)$"
)


@dataclass(frozen=True)
class MonitorEvent:
    timestamp: str | None
    kind: str
    level: str
    logger: str
    message: str


@dataclass(frozen=True)
class ContextEvent:
    timestamp: str | None
    depth: str
    elapsed_ms: float
    budget_ms: float
    timed_out: bool
    source_counts: dict[str, int]
    message: str


@dataclass(frozen=True)
class ParsedServerDebug:
    timeline: list[MonitorEvent]
    latest_context: ContextEvent | None
    category_counts: dict[str, int]


@dataclass(frozen=True)
class BackendTraceCall:
    event: str
    role: str | None
    kind: str | None
    backend: str | None
    model: str | None
    request_id: str | None
    total_ms: float | None
    first_ms: float | None
    raw: dict[str, Any]


def build_monitor_snapshot(
    *,
    server_log_path: Path,
    backend_trace_path: Path,
    config_path: Path | None,
    log_tail_lines: int = 2000,
) -> dict[str, Any]:
    server_lines = _read_if_exists(server_log_path, max_lines=log_tail_lines)
    trace_lines = _read_if_exists(backend_trace_path, max_lines=log_tail_lines)
    parsed_log = parse_server_debug_lines(server_lines)
    recent_calls = parse_backend_trace_lines(trace_lines, limit=80)
    database = read_database_summary(config_path)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sources": {
            "server_log": str(server_log_path),
            "backend_trace": str(backend_trace_path),
            "config": str(config_path) if config_path is not None else None,
        },
        "timeline": [asdict(event) for event in parsed_log.timeline[-120:]],
        "categories": parsed_log.category_counts,
        "context": {
            "latest": asdict(parsed_log.latest_context)
            if parsed_log.latest_context is not None
            else None,
        },
        "backend_trace": {
            "recent_calls": [asdict(call) for call in recent_calls],
            "role_counts": dict(Counter(call.role or "unknown" for call in recent_calls)),
        },
        "database": database,
    }


def parse_server_debug_lines(lines: list[str]) -> ParsedServerDebug:
    timeline: list[MonitorEvent] = []
    latest_context: ContextEvent | None = None
    counts: Counter[str] = Counter()
    interesting = {
        "error",
        "warning",
        "transcript",
        "participation",
        "conversation_prompt",
        "reply",
        "initiative",
        "turn_taking",
        "context",
        "memory",
        "backend",
        "playback",
    }
    for raw in lines:
        parsed = _parse_log_line(raw)
        category = classify_event(
            level=parsed["level"],
            logger=parsed["logger"],
            message=parsed["message"],
            raw=raw,
        )
        context_event = parse_context_event(raw)
        if context_event is not None:
            category = "context"
            latest_context = context_event
        counts[category] += 1
        if category in interesting:
            timeline.append(
                MonitorEvent(
                    timestamp=parsed["timestamp"],
                    kind=category,
                    level=parsed["level"],
                    logger=parsed["logger"],
                    message=parsed["message"],
                )
            )
    return ParsedServerDebug(
        timeline=timeline,
        latest_context=latest_context,
        category_counts=dict(counts),
    )


def parse_context_event(raw: str) -> ContextEvent | None:
    match = CONTEXT_RE.search(raw)
    if match is None:
        return None
    parsed = _parse_log_line(raw)
    return ContextEvent(
        timestamp=parsed["timestamp"],
        depth=match.group("depth"),
        elapsed_ms=float(match.group("elapsed")),
        budget_ms=float(match.group("budget")),
        timed_out=match.group("timed_out") == "True",
        source_counts={
            count_match.group("key"): int(count_match.group("value"))
            for count_match in COUNT_RE.finditer(raw)
        },
        message=parsed["message"],
    )


def parse_backend_trace_lines(lines: list[str], *, limit: int) -> list[BackendTraceCall]:
    calls: list[BackendTraceCall] = []
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or payload.get("trace") != "tomoko_backend_call":
            continue
        calls.append(
            BackendTraceCall(
                event=str(payload.get("event") or ""),
                role=_optional_str(payload.get("role")),
                kind=_optional_str(payload.get("kind")),
                backend=_optional_str(payload.get("backend")),
                model=_optional_str(payload.get("model")),
                request_id=_optional_str(payload.get("request_id")),
                total_ms=_optional_float(payload.get("total_ms")),
                first_ms=_optional_float(payload.get("first_ms")),
                raw=payload,
            )
        )
    return calls[-limit:]


def read_database_summary(config_path: Path | None) -> dict[str, Any]:
    if config_path is None:
        return {"available": False, "reason": "config_not_provided"}
    try:
        import psycopg
        from psycopg.rows import dict_row

        from server.shared.config import NodeConfig

        config = NodeConfig.load(config_path)
        with psycopg.connect(config.database.dsn, row_factory=dict_row) as conn:
            return {
                "available": True,
                "utterance_candidates": _candidate_counts(conn, "utterance_candidates"),
                "arrival_candidates": _arrival_counts(conn),
                "conversation_sessions": _simple_count(conn, "conversation_sessions"),
                "conversation_logs": _simple_count(conn, "conversation_logs"),
                "stop_intent_observations": _simple_count(conn, "stop_intent_observations"),
            }
    except Exception as exc:
        return {
            "available": False,
            "reason": type(exc).__name__,
            "detail": str(exc),
        }


def _candidate_counts(conn: Any, table: str) -> dict[str, int]:
    del table
    row = conn.execute(
        """
        SELECT
          count(*)::int AS total,
          count(*) FILTER (
            WHERE spoken_at IS NULL AND dismissed_at IS NULL AND expires_at > now()
          )::int AS active,
          count(*) FILTER (WHERE spoken_at IS NOT NULL)::int AS spoken,
          count(*) FILTER (WHERE dismissed_at IS NOT NULL)::int AS dismissed
        FROM utterance_candidates
        """
    ).fetchone()
    return dict(row or {})


def _arrival_counts(conn: Any) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT
          count(*)::int AS total,
          count(*) FILTER (WHERE used_at IS NULL AND valid_until > now())::int AS fresh,
          count(*) FILTER (WHERE used_at IS NOT NULL)::int AS used
        FROM arrival_candidates
        """
    ).fetchone()
    return dict(row or {})


def _simple_count(conn: Any, table: str) -> dict[str, int]:
    row = conn.execute(f"SELECT count(*)::int AS total FROM {table}").fetchone()
    return dict(row or {})


def _read_if_exists(path: Path, *, max_lines: int) -> list[str]:
    if not path.exists():
        return []
    return read_tail_lines(path, max_lines)


def _parse_log_line(raw: str) -> dict[str, str | None]:
    match = LOG_RE.match(raw)
    if match is not None:
        return {
            "timestamp": match.group("timestamp"),
            "level": match.group("level"),
            "logger": match.group("logger"),
            "message": match.group("message"),
        }
    if raw.startswith("WARNING:"):
        return {
            "timestamp": None,
            "level": "WARNING",
            "logger": "uvicorn",
            "message": raw.removeprefix("WARNING:").strip(),
        }
    if raw.startswith("INFO:"):
        return {
            "timestamp": None,
            "level": "INFO",
            "logger": "uvicorn",
            "message": raw.removeprefix("INFO:").strip(),
        }
    return {
        "timestamp": None,
        "level": "RAW",
        "logger": "raw",
        "message": raw,
    }


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
