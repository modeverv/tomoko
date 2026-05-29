from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from server.shared.models import (
    CalendarEvent,
    MemoryHit,
    SessionSummaryHit,
    TomokoContextSnapshot,
)

CALENDAR_CONTEXT_TIMEZONE = ZoneInfo("Asia/Tokyo")


def session_summary_hit_to_memory(hit: SessionSummaryHit) -> MemoryHit:
    return MemoryHit(
        speaker="tomoko",
        text=f"会話セッション要約: {hit.summary_text}",
        timestamp=hit.ended_at or hit.started_at,
        similarity=hit.similarity,
        source_id=f"session_summary:{hit.session_id}",
    )


def context_snapshot_long_term_memory(
    snapshot: TomokoContextSnapshot,
) -> list[MemoryHit]:
    memories = [
        session_summary_hit_to_memory(hit)
        for hit in snapshot.session_summaries
    ]
    memories.extend(snapshot.memory_hits)
    return memories


def context_snapshot_calendar_memory(
    snapshot: TomokoContextSnapshot,
) -> list[MemoryHit]:
    return [
        calendar_event_to_memory(event)
        for event in snapshot.calendar_events
        if event.status.lower() != "cancelled"
    ]


def calendar_event_to_memory(event: CalendarEvent) -> MemoryHit:
    return MemoryHit(
        speaker="tomoko",
        text=f"カレンダー予定: {_format_calendar_event_for_memory(event)}",
        timestamp=event.start_time,
        similarity=1.0,
        source_id=f"calendar:{event.source_key}",
    )


def _format_calendar_event_for_memory(event: CalendarEvent) -> str:
    start = _calendar_time_text(event.start_time, all_day=event.all_day)
    if event.all_day:
        time_text = f"{start} 終日"
    elif event.end_time is not None and _same_local_date(
        event.start_time,
        event.end_time,
    ):
        end = event.end_time.astimezone(CALENDAR_CONTEXT_TIMEZONE).strftime("%H:%M")
        time_text = f"{start}-{end}"
    else:
        time_text = start

    detail = event.summary
    if event.location:
        detail = f"{detail} @ {event.location}"
    return f"{time_text}: {detail}"


def _same_local_date(left: datetime, right: datetime) -> bool:
    left_local = (
        left.astimezone(CALENDAR_CONTEXT_TIMEZONE) if left.tzinfo is not None else left
    )
    right_local = (
        right.astimezone(CALENDAR_CONTEXT_TIMEZONE)
        if right.tzinfo is not None
        else right
    )
    return left_local.date() == right_local.date()


def _calendar_time_text(value: datetime, *, all_day: bool) -> str:
    local_value = (
        value.astimezone(CALENDAR_CONTEXT_TIMEZONE)
        if value.tzinfo is not None
        else value
    )
    if all_day:
        return local_value.strftime("%Y-%m-%d")
    return local_value.strftime("%Y-%m-%d %H:%M")
