"""Calendar materials for Tomoko v2 speech decisions.

Calendar items are held as a ``{"YYYY-MM-DD HH:MM": title}`` map (see
ARCHITECTURE.md). Urgency rises linearly as an upcoming event approaches within
``near_window_min`` minutes and is 0 outside it. The append notice text is a
deterministic template for v0; moving it to LLM generation is a later step.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from server.shared.models import utc_now

CALENDAR_KEY_FORMAT = "%Y-%m-%d %H:%M"
NEAR_WINDOW_MIN = 30.0
CALENDAR_APPEND_THRESHOLD = 0.6


def parse_calendar_key(key: str) -> datetime | None:
    try:
        return datetime.strptime(key, CALENDAR_KEY_FORMAT)
    except ValueError:
        return None


def _as_naive(moment: datetime) -> datetime:
    return moment.replace(tzinfo=None)


def calendar_urgency_from_items(
    items: dict[str, str],
    *,
    now: datetime,
    near_window_min: float = NEAR_WINDOW_MIN,
) -> float:
    reference = _as_naive(now)
    urgency = 0.0
    for key in items:
        starts_at = parse_calendar_key(key)
        if starts_at is None:
            continue
        minutes_until = (starts_at - reference).total_seconds() / 60.0
        if minutes_until < 0 or minutes_until > near_window_min:
            continue
        urgency = max(urgency, 1.0 - minutes_until / near_window_min)
    return urgency


def next_calendar_item(
    items: dict[str, str],
    *,
    now: datetime,
    near_window_min: float = NEAR_WINDOW_MIN,
) -> tuple[str, str] | None:
    reference = _as_naive(now)
    upcoming: list[tuple[datetime, str, str]] = []
    for key, title in items.items():
        starts_at = parse_calendar_key(key)
        if starts_at is None:
            continue
        minutes_until = (starts_at - reference).total_seconds() / 60.0
        if minutes_until < 0 or minutes_until > near_window_min:
            continue
        upcoming.append((starts_at, key, title))
    if not upcoming:
        return None
    upcoming.sort()
    _, key, title = upcoming[0]
    return key, title


def calendar_notice_text(starts_at_key: str, title: str) -> str:
    starts_at = parse_calendar_key(starts_at_key)
    time_label = starts_at.strftime("%H:%M") if starts_at is not None else starts_at_key
    return f"ところで、{time_label}から{title}の予定があるよ。"


def fake_calendar_provider_from_env_payload(
    payload: list[dict[str, object]],
) -> Callable[[], dict[str, str]]:
    """Build a calendar provider from ``TOMOKO_V2_FAKE_CALENDAR`` entries.

    Each entry is ``{"offset_min": minutes-from-now, "title": text}``. The
    provider recomputes absolute times on every call so the offset stays fixed
    relative to the current moment, which keeps replay scenarios deterministic.
    """

    entries = [
        (float(str(item.get("offset_min", 0))), str(item.get("title", "予定")))
        for item in payload
    ]

    def provider() -> dict[str, str]:
        now = _as_naive(utc_now())
        return {
            (now + timedelta(minutes=offset)).strftime(CALENDAR_KEY_FORMAT): title
            for offset, title in entries
        }

    return provider
