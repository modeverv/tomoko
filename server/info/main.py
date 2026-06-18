from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    starts_at: str
    title: str


def parse_minimal_ics(text: str) -> list[CalendarEvent]:
    events: list[CalendarEvent] = []
    current_start: str | None = None
    current_title: str | None = None
    for line in text.splitlines():
        if line.startswith("DTSTART"):
            current_start = line.split(":", 1)[1].strip()
        elif line.startswith("SUMMARY"):
            current_title = line.split(":", 1)[1].strip()
        elif line.strip() == "END:VEVENT" and current_start and current_title:
            events.append(CalendarEvent(starts_at=current_start, title=current_title))
            current_start = None
            current_title = None
    return events


def calendar_dto_map(events: list[CalendarEvent]) -> dict[str, str]:
    return {event.starts_at: event.title for event in events}


def should_candidate_from_world(
    *,
    confidence: float,
    stale: bool,
    sensitive: bool,
    private: bool,
    do_not_speak: bool,
) -> bool:
    return confidence >= 0.65 and not (stale or sensitive or private or do_not_speak)
