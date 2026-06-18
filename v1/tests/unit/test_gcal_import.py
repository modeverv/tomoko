from __future__ import annotations

from datetime import UTC, datetime

import pytest

from server.shared.calendar import (
    InMemoryCalendarEventStore,
    parse_ics_events,
    read_calendar_urls_file,
    source_id_for_url,
)

ICS_SAMPLE = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:meeting-1@example.com
DTSTART;TZID=Asia/Tokyo:20260530T130000
DTEND;TZID=Asia/Tokyo:20260530T140000
SUMMARY:Deep context meeting
DESCRIPTION:Calendar details for Tomoko.
LOCATION:Kitchen
END:VEVENT
BEGIN:VEVENT
UID:plan-1@example.com
DTSTART;VALUE=DATE:20260531
SUMMARY:All day plan
END:VEVENT
BEGIN:VEVENT
UID:weekly-1@example.com
DTSTART;TZID=Asia/Tokyo:20260530T090000
DTEND;TZID=Asia/Tokyo:20260530T093000
RRULE:FREQ=WEEKLY;COUNT=2;BYDAY=SA
SUMMARY:Weekly check
END:VEVENT
BEGIN:VEVENT
UID:anniversary-1@example.com
DTSTART;VALUE=DATE:20140529
DTEND;VALUE=DATE:20140530
RRULE:FREQ=YEARLY
SUMMARY:Yearly anniversary
END:VEVENT
END:VCALENDAR
"""


@pytest.mark.unit
def test_parse_ics_events_expands_timed_all_day_and_weekly_events() -> None:
    events = parse_ics_events(
        ICS_SAMPLE,
        source_id="src",
        now=datetime(2026, 5, 30, 0, 0, tzinfo=UTC),
        days_before=0,
        days_ahead=14,
    )

    assert [event.summary for event in events] == [
        "Weekly check",
        "Deep context meeting",
        "All day plan",
        "Weekly check",
    ]
    meeting = events[1]
    assert meeting.location == "Kitchen"
    assert meeting.description == "Calendar details for Tomoko."
    assert meeting.start_time.isoformat() == "2026-05-30T04:00:00+00:00"
    assert meeting.end_time is not None
    assert meeting.end_time.isoformat() == "2026-05-30T05:00:00+00:00"
    assert events[2].all_day is True


@pytest.mark.unit
def test_parse_ics_events_expands_yearly_event_once_per_year() -> None:
    events = parse_ics_events(
        ICS_SAMPLE,
        source_id="src",
        now=datetime(2026, 5, 30, 0, 0, tzinfo=UTC),
        days_before=2,
        days_ahead=14,
    )

    yearly_events = [
        event for event in events if event.summary == "Yearly anniversary"
    ]
    assert len(yearly_events) == 1
    assert yearly_events[0].start_time.isoformat() == "2026-05-29T00:00:00+09:00"


@pytest.mark.unit
def test_parse_ics_events_does_not_expand_unknown_rrule_daily() -> None:
    text = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:unsupported-1@example.com
DTSTART;VALUE=DATE:20260529
RRULE:FREQ=HOURLY
SUMMARY:Unsupported recurrence
END:VEVENT
END:VCALENDAR
"""
    events = parse_ics_events(
        text,
        source_id="src",
        now=datetime(2026, 5, 30, 0, 0, tzinfo=UTC),
        days_before=2,
        days_ahead=14,
    )

    assert [event.summary for event in events] == ["Unsupported recurrence"]


@pytest.mark.unit
def test_parse_ics_events_treats_event_end_as_exclusive() -> None:
    text = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:yesterday-1@example.com
DTSTART;VALUE=DATE:20260529
DTEND;VALUE=DATE:20260530
SUMMARY:Yesterday all day
END:VEVENT
END:VCALENDAR
"""
    events = parse_ics_events(
        text,
        source_id="src",
        now=datetime(2026, 5, 30, 0, 0, tzinfo=UTC),
        days_before=0,
        days_ahead=1,
    )

    assert events == []


@pytest.mark.unit
async def test_calendar_store_replaces_source_and_reads_context_window() -> None:
    store = InMemoryCalendarEventStore()
    events = parse_ics_events(
        ICS_SAMPLE,
        source_id="src",
        now=datetime(2026, 5, 30, 0, 0, tzinfo=UTC),
        days_before=0,
        days_ahead=14,
    )

    inserted = await store.replace_source_events(source_id="src", events=events)
    context_events = await store.read_context_events(
        now=datetime(2026, 5, 30, 0, 0, tzinfo=UTC),
        days_before=0,
        days_ahead=2,
        limit=10,
    )

    assert inserted == 4
    assert [event.summary for event in context_events] == [
        "Weekly check",
        "Deep context meeting",
        "All day plan",
    ]


@pytest.mark.unit
def test_gcal_urls_file_skips_blank_lines_and_comments(tmp_path) -> None:
    file = tmp_path / "gcal_urls.txt"
    file.write_text(
        "\n# private URLs live outside git\nhttps://example.com/basic.ics\n",
        encoding="utf-8",
    )

    assert read_calendar_urls_file(file) == ["https://example.com/basic.ics"]
    assert source_id_for_url("https://example.com/basic.ics")
