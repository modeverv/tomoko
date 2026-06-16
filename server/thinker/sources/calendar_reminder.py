from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from server.shared.calendar import CalendarEventStore
from server.shared.candidate import CandidateSeed, ThinkerSourceContext
from server.shared.models import CalendarEvent

_SOURCE_NAME = "calendar_reminder"
_READ_DAYS_AHEAD = 1


@dataclass(frozen=True)
class CalendarReminderWindow:
    name: str
    offset: timedelta
    next_offset: timedelta | None
    label: str
    priority: float
    urgent: bool
    ttl_after_start: timedelta = timedelta(minutes=10)


_WINDOWS = (
    CalendarReminderWindow(
        name="fifteen_min",
        offset=timedelta(minutes=15),
        next_offset=timedelta(minutes=5),
        label="15分後",
        priority=0.65,
        urgent=False,
    ),
    CalendarReminderWindow(
        name="five_min",
        offset=timedelta(minutes=5),
        next_offset=timedelta(0),
        label="5分後",
        priority=0.85,
        urgent=True,
    ),
    CalendarReminderWindow(
        name="due",
        offset=timedelta(0),
        next_offset=None,
        label="今",
        priority=0.95,
        urgent=True,
    ),
)


class CalendarReminderSource:
    def __init__(self, *, calendar_store: CalendarEventStore) -> None:
        self.calendar_store = calendar_store

    async def collect(self, context: ThinkerSourceContext) -> list[CandidateSeed]:
        events = await self.calendar_store.read_context_events(
            now=context.observed_at,
            days_before=0,
            days_ahead=_READ_DAYS_AHEAD,
            limit=32,
        )
        seeds: list[CandidateSeed] = []
        for event in events:
            window = _active_window(event, context.observed_at)
            if window is None:
                continue
            seeds.append(
                CandidateSeed(
                    seed_text=_seed_text(event, window),
                    source=_SOURCE_NAME,
                    priority=window.priority,
                    urgent=window.urgent,
                    expires_at=_expires_at(event, window),
                    dedupe_key=f"{_SOURCE_NAME}:{event.source_key}:{window.name}",
                    context_tags=(
                        "calendar",
                        f"calendar_window:{window.name}",
                    ),
                    metadata_json={
                        "schema_version": 1,
                        "event_source_id": event.source_id,
                        "event_uid": event.uid,
                        "event_start_time": event.start_time.isoformat(),
                        "event_end_time": event.end_time.isoformat()
                        if event.end_time is not None
                        else None,
                        "event_summary": event.summary,
                        "event_location": event.location,
                        "reminder_window": window.name,
                        "available_at": (event.start_time - window.offset).isoformat(),
                    },
                )
            )
        return seeds


def _active_window(
    event: CalendarEvent,
    observed_at: datetime,
) -> CalendarReminderWindow | None:
    if event.all_day:
        return None
    for window in _WINDOWS:
        available_at = event.start_time - window.offset
        if observed_at < available_at:
            continue
        expires_at = _expires_at(event, window)
        if observed_at < expires_at:
            return window
    return None


def _expires_at(event: CalendarEvent, window: CalendarReminderWindow) -> datetime:
    if window.next_offset is None:
        return event.start_time + window.ttl_after_start
    return event.start_time - window.next_offset


def _seed_text(event: CalendarEvent, window: CalendarReminderWindow) -> str:
    location = f" 場所: {event.location}" if event.location else ""
    if window.name == "due":
        return f"予定「{event.summary}」が今始まる。必要なら短く知らせる。{location}".strip()
    return (
        f"予定「{event.summary}」が{window.label}に始まる。"
        f"必要なら短く知らせる。{location}"
    ).strip()
