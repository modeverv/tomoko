from __future__ import annotations

import calendar
import hashlib
import urllib.request
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

import psycopg

from server.shared.models import CalendarEvent

CALENDAR_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS calendar_events (
    source_id TEXT NOT NULL,
    uid TEXT NOT NULL,
    summary TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ,
    all_day BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'confirmed',
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw_event_hash TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (source_id, uid, start_time)
);

CREATE INDEX IF NOT EXISTS idx_calendar_events_context
    ON calendar_events (start_time, end_time);
"""

DEFAULT_TIMEZONE = ZoneInfo("Asia/Tokyo")
WEEKDAY_NUMBERS = {
    "SU": 6,
    "MO": 0,
    "TU": 1,
    "WE": 2,
    "TH": 3,
    "FR": 4,
    "SA": 5,
}


class CalendarEventStore(Protocol):
    async def read_context_events(
        self,
        *,
        now: datetime,
        days_before: int,
        days_ahead: int,
        limit: int,
    ) -> list[CalendarEvent]: ...

    async def replace_source_events(
        self,
        *,
        source_id: str,
        events: list[CalendarEvent],
    ) -> int: ...


class InMemoryCalendarEventStore:
    def __init__(self) -> None:
        self.events: list[CalendarEvent] = []

    async def read_context_events(
        self,
        *,
        now: datetime,
        days_before: int,
        days_ahead: int,
        limit: int,
    ) -> list[CalendarEvent]:
        start, end = _context_window(now, days_before, days_ahead)
        selected = [
            event
            for event in self.events
            if _event_overlaps(event, start=start, end=end)
            and event.status.lower() != "cancelled"
        ]
        return sorted(selected, key=lambda event: event.start_time)[:limit]

    async def replace_source_events(
        self,
        *,
        source_id: str,
        events: list[CalendarEvent],
    ) -> int:
        self.events = [event for event in self.events if event.source_id != source_id]
        self.events.extend(events)
        return len(events)


class PostgresCalendarEventStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def ensure_schema(self) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(CALENDAR_SCHEMA_SQL)

    async def read_context_events(
        self,
        *,
        now: datetime,
        days_before: int,
        days_ahead: int,
        limit: int,
    ) -> list[CalendarEvent]:
        start, end = _context_window(now, days_before, days_ahead)
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT source_id, uid, summary, description, location,
                           start_time, end_time, all_day, status
                    FROM calendar_events
                    WHERE status <> 'cancelled'
                      AND start_time < %s
                      AND COALESCE(end_time, start_time) > %s
                    ORDER BY start_time ASC
                    LIMIT %s
                    """,
                    (end, start, limit),
                )
                rows = await cur.fetchall()
        return [
            CalendarEvent(
                source_id=str(row[0]),
                uid=str(row[1]),
                summary=str(row[2]),
                description=str(row[3] or ""),
                location=str(row[4] or ""),
                start_time=_as_datetime(row[5]),
                end_time=_optional_datetime(row[6]),
                all_day=bool(row[7]),
                status=str(row[8] or "confirmed"),
            )
            for row in rows
        ]

    async def replace_source_events(
        self,
        *,
        source_id: str,
        events: list[CalendarEvent],
    ) -> int:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM calendar_events WHERE source_id = %s", (source_id,))
                for event in events:
                    await cur.execute(
                        """
                        INSERT INTO calendar_events (
                            source_id, uid, summary, description, location,
                            start_time, end_time, all_day, status, raw_event_hash
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            event.source_id,
                            event.uid,
                            event.summary,
                            event.description,
                            event.location,
                            event.start_time,
                            event.end_time,
                            event.all_day,
                            event.status,
                            _event_hash(event),
                        ),
                    )
        return len(events)


class CalendarIcsImporter:
    def __init__(
        self,
        *,
        store: CalendarEventStore,
        days_before: int = 1,
        days_ahead: int = 30,
    ) -> None:
        self.store = store
        self.days_before = days_before
        self.days_ahead = days_ahead

    async def import_urls(self, urls: Iterable[str], *, now: datetime | None = None) -> int:
        imported = 0
        now = now or datetime.now(UTC)
        for url in urls:
            source_id = source_id_for_url(url)
            text = fetch_ics_url(url)
            events = parse_ics_events(
                text,
                source_id=source_id,
                now=now,
                days_before=self.days_before,
                days_ahead=self.days_ahead,
            )
            imported += await self.store.replace_source_events(
                source_id=source_id,
                events=events,
            )
        return imported


def read_calendar_urls_file(path: str | Path) -> list[str]:
    file_path = Path(path).expanduser()
    if not file_path.exists():
        return []
    urls: list[str] = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            urls.append(stripped)
    return urls


def fetch_ics_url(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read().decode("utf-8-sig")


def source_id_for_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def parse_ics_events(
    text: str,
    *,
    source_id: str,
    now: datetime | None = None,
    days_before: int = 1,
    days_ahead: int = 30,
) -> list[CalendarEvent]:
    now = now or datetime.now(UTC)
    start, end = _context_window(now, days_before, days_ahead)
    events: list[CalendarEvent] = []
    for raw_event in _raw_ics_events(text):
        for event in _expand_raw_event(raw_event, source_id=source_id, range_end=end):
            if _event_overlaps(event, start=start, end=end):
                events.append(event)
    return sorted(events, key=lambda event: event.start_time)


def _raw_ics_events(text: str) -> list[list[tuple[str, dict[str, str], str]]]:
    events: list[list[tuple[str, dict[str, str], str]]] = []
    current: list[tuple[str, dict[str, str], str]] = []
    in_event = False
    for line in _unfold_ics_lines(text):
        parsed = _parse_content_line(line)
        if parsed is None:
            continue
        name, params, value = parsed
        if name == "BEGIN" and value == "VEVENT":
            current = []
            in_event = True
            continue
        if name == "END" and value == "VEVENT":
            if in_event:
                events.append(current)
            current = []
            in_event = False
            continue
        if in_event:
            current.append((name, params, value))
    return events


def _expand_raw_event(
    raw_event: list[tuple[str, dict[str, str], str]],
    *,
    source_id: str,
    range_end: datetime,
) -> list[CalendarEvent]:
    start_raw = _field_value(raw_event, "DTSTART")
    if start_raw is None:
        return []
    start_time, all_day = _parse_ics_time(start_raw, _field_params(raw_event, "DTSTART"))
    base = CalendarEvent(
        source_id=source_id,
        uid=_field_value(raw_event, "UID") or f"no-uid-{_event_raw_hash(raw_event)}",
        summary=_ical_unescape(_field_value(raw_event, "SUMMARY") or "(No title)"),
        description=_ical_unescape(_field_value(raw_event, "DESCRIPTION") or ""),
        location=_ical_unescape(_field_value(raw_event, "LOCATION") or ""),
        start_time=start_time,
        end_time=_parse_optional_ics_time(
            _field_value(raw_event, "DTEND"),
            _field_params(raw_event, "DTEND"),
        ),
        all_day=all_day,
        status=(_field_value(raw_event, "STATUS") or "confirmed").lower(),
    )
    rrule = _field_value(raw_event, "RRULE")
    if not rrule:
        return [base]
    return _expand_rrule(base, rrule, range_end=range_end)


def _expand_rrule(base: CalendarEvent, rrule: str, *, range_end: datetime) -> list[CalendarEvent]:
    rule = _parse_rrule(rrule)
    freq = rule.get("FREQ", "DAILY")
    if freq not in {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}:
        return [base]
    interval = max(1, int(rule.get("INTERVAL", "1")))
    count = int(rule["COUNT"]) if "COUNT" in rule else None
    until = _parse_optional_ics_time(rule.get("UNTIL"), {}) if "UNTIL" in rule else None
    byday = _parse_byday(rule.get("BYDAY"))
    duration = (
        base.end_time - base.start_time
        if base.end_time is not None
        else None
    )
    cursor = base.start_time
    occurrences = 0
    events: list[CalendarEvent] = []
    while cursor < range_end:
        for start_time in _rrule_occurrences_for_step(cursor, freq=freq, byday=byday):
            if start_time < base.start_time:
                continue
            if until is not None and start_time > until:
                return events
            occurrences += 1
            events.append(
                replace(
                    base,
                    start_time=start_time,
                    end_time=start_time + duration if duration is not None else None,
                )
            )
            if count is not None and occurrences >= count:
                return events
        cursor = _advance_time(cursor, freq=freq, interval=interval)
    return events


def _rrule_occurrences_for_step(
    cursor: datetime,
    *,
    freq: str,
    byday: list[int] | None,
) -> list[datetime]:
    if freq == "WEEKLY" and byday:
        return sorted(
            (cursor + timedelta(days=day - cursor.weekday()) for day in byday),
            key=lambda value: value,
        )
    return [cursor]


def _advance_time(cursor: datetime, *, freq: str, interval: int) -> datetime:
    if freq == "WEEKLY":
        return cursor + timedelta(days=7 * interval)
    if freq == "DAILY":
        return cursor + timedelta(days=interval)
    if freq == "MONTHLY":
        return _add_months(cursor, interval)
    if freq == "YEARLY":
        return _add_months(cursor, 12 * interval)
    return cursor + timedelta(days=interval)


def _add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _context_window(
    now: datetime,
    days_before: int,
    days_ahead: int,
) -> tuple[datetime, datetime]:
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    local_now = now.astimezone(DEFAULT_TIMEZONE)
    today_start = datetime.combine(local_now.date(), time.min, DEFAULT_TIMEZONE)
    return (
        today_start - timedelta(days=days_before),
        today_start + timedelta(days=days_ahead),
    )


def _event_overlaps(event: CalendarEvent, *, start: datetime, end: datetime) -> bool:
    event_end = event.end_time or event.start_time
    return event.start_time < end and event_end > start


def _unfold_ics_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw_line.startswith((" ", "\t")) and lines:
            lines[-1] += raw_line[1:]
        else:
            lines.append(raw_line)
    return lines


def _parse_content_line(line: str) -> tuple[str, dict[str, str], str] | None:
    if ":" not in line:
        return None
    left, value = line.split(":", 1)
    parts = left.split(";")
    params: dict[str, str] = {}
    for part in parts[1:]:
        if "=" in part:
            key, param_value = part.split("=", 1)
            params[key.upper()] = param_value
    return parts[0].upper(), params, value


def _field_value(
    event: list[tuple[str, dict[str, str], str]],
    name: str,
) -> str | None:
    for field_name, _params, value in event:
        if field_name == name:
            return value
    return None


def _field_params(
    event: list[tuple[str, dict[str, str], str]],
    name: str,
) -> dict[str, str]:
    for field_name, params, _value in event:
        if field_name == name:
            return params
    return {}


def _parse_optional_ics_time(value: str | None, params: dict[str, str]) -> datetime | None:
    if value is None:
        return None
    parsed, _all_day = _parse_ics_time(value, params)
    return parsed


def _parse_ics_time(value: str, params: dict[str, str]) -> tuple[datetime, bool]:
    if params.get("VALUE") == "DATE" or (len(value) == 8 and value.isdigit()):
        return (
            datetime(
                int(value[0:4]),
                int(value[4:6]),
                int(value[6:8]),
                tzinfo=DEFAULT_TIMEZONE,
            ),
            True,
        )
    zone = UTC if value.endswith("Z") else ZoneInfo(params.get("TZID", "Asia/Tokyo"))
    compact = value.removesuffix("Z")
    parsed = datetime.strptime(compact, "%Y%m%dT%H%M%S").replace(tzinfo=zone)
    return parsed.astimezone(UTC), False


def _parse_rrule(rrule: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in rrule.split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            result[key.upper()] = value
    return result


def _parse_byday(value: str | None) -> list[int] | None:
    if value is None:
        return None
    return [
        WEEKDAY_NUMBERS[day[-2:]]
        for day in value.split(",")
        if day[-2:] in WEEKDAY_NUMBERS
    ]


def _ical_unescape(text: str) -> str:
    return (
        text.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _event_raw_hash(event: list[tuple[str, dict[str, str], str]]) -> str:
    return hashlib.sha1(repr(event).encode("utf-8")).hexdigest()[:12]


def _event_hash(event: CalendarEvent) -> str:
    return hashlib.sha1(
        "|".join(
            (
                event.uid,
                event.summary,
                event.start_time.isoformat(),
                event.end_time.isoformat() if event.end_time else "",
            )
        ).encode("utf-8")
    ).hexdigest()


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    return _as_datetime(value)
