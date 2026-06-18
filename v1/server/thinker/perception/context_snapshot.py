from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from server.shared.calendar import CalendarEventStore
from server.shared.models import (
    CalendarEvent,
    HumanActivityObservation,
    HumanPresenceObservation,
    InteractionReadiness,
    ScreenActivityObservation,
    UserContextSnapshot,
    WorldObservationInterpretationRecord,
)
from server.shared.perception import (
    HumanActivityObservationStore,
    HumanPresenceObservationStore,
    ScreenActivityObservationStore,
    UserContextSnapshotStore,
)
from server.thinker.perception.activity import coherent_activity_label

logger = logging.getLogger(__name__)


class WorldContextStore(Protocol):
    async def fetch_candidate_interpretations(
        self,
        *,
        limit: int,
        min_confidence: float = 0.45,
        min_interest: float = 0.45,
    ) -> tuple[WorldObservationInterpretationRecord, ...]: ...


@dataclass(frozen=True)
class UserContextSnapshotTrace:
    elapsed_ms: float
    source_counts: dict[str, int]
    skipped_sources: tuple[str, ...] = ()
    source_errors: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class UserContextSnapshotBuildResult:
    snapshot: UserContextSnapshot
    trace: UserContextSnapshotTrace


class UserContextSnapshotBuilder:
    def __init__(
        self,
        *,
        snapshot_store: UserContextSnapshotStore,
        presence_store: HumanPresenceObservationStore | None = None,
        activity_store: HumanActivityObservationStore | None = None,
        screen_store: ScreenActivityObservationStore | None = None,
        calendar_store: CalendarEventStore | None = None,
        world_store: WorldContextStore | None = None,
        device_id: str | None = None,
        model: str = "deterministic-v1",
    ) -> None:
        self.snapshot_store = snapshot_store
        self.presence_store = presence_store
        self.activity_store = activity_store
        self.screen_store = screen_store
        self.calendar_store = calendar_store
        self.world_store = world_store
        self.device_id = device_id
        self.model = model

    async def build_once(
        self,
        *,
        now: datetime | None = None,
    ) -> UserContextSnapshotBuildResult:
        computed_at = now or datetime.now(UTC)
        started_at = time.perf_counter()
        skipped_sources: list[str] = []
        source_errors: dict[str, str] = {}

        presence = await self._load_latest_presence(
            skipped_sources=skipped_sources,
            source_errors=source_errors,
        )
        activity = await self._load_latest_activity(
            skipped_sources=skipped_sources,
            source_errors=source_errors,
        )
        screen = await self._load_latest_screen(
            skipped_sources=skipped_sources,
            source_errors=source_errors,
        )
        calendar_events = await self._load_calendar_events(
            now=computed_at,
            skipped_sources=skipped_sources,
            source_errors=source_errors,
        )
        world_records = await self._load_world_records(
            skipped_sources=skipped_sources,
            source_errors=source_errors,
        )

        activity_label = coherent_activity_label(
            present=presence.present if presence is not None else None,
            activity_label=activity.activity_label if activity is not None else None,
        )
        screen_label = (
            screen.screen_activity_label if screen is not None else None
        )
        readiness = decide_interaction_readiness(
            present=presence.present if presence is not None else None,
            activity_label=activity_label,
            screen_activity_label=screen_label,
        )
        calendar_summary = _calendar_summary(calendar_events, now=computed_at)
        world_summary = _world_summary(world_records)
        source_frame_ids = _source_frame_ids(presence, activity, screen)
        source_observation_ids = _source_observation_ids(presence, activity, screen)
        source_counts = {
            "presence": 1 if presence is not None else 0,
            "activity": 1 if activity is not None else 0,
            "screen": 1 if screen is not None else 0,
            "calendar": len(calendar_events),
            "world": len(world_records),
        }
        user_activity_summary = _user_activity_summary(
            present=presence.present if presence is not None else None,
            activity_label=activity_label,
            screen_activity_label=screen_label,
        )
        context_summary = _context_summary(
            user_activity_summary=user_activity_summary,
            calendar_summary=calendar_summary,
            world_summary=world_summary,
            readiness=readiness,
        )
        snapshot = await self.snapshot_store.insert_snapshot(
            UserContextSnapshot(
                computed_at=computed_at,
                device_id=self.device_id,
                present=presence.present if presence is not None else None,
                presence_observed_at=presence.observed_at
                if presence is not None
                else None,
                activity_label=activity_label,
                activity_observed_at=activity.observed_at
                if activity is not None
                else None,
                screen_activity_label=screen_label,
                screen_observed_at=screen.observed_at if screen is not None else None,
                calendar_summary=calendar_summary,
                world_summary=world_summary,
                user_activity_summary=user_activity_summary,
                context_summary=context_summary,
                interaction_readiness=readiness,
                confidence=_confidence(presence, activity, screen),
                source_frame_ids=source_frame_ids,
                source_observation_ids=source_observation_ids,
                model=self.model,
                raw_reason_json={
                    "source_counts": source_counts,
                    "skipped_sources": skipped_sources,
                    "source_errors": source_errors,
                },
            )
        )
        trace = UserContextSnapshotTrace(
            elapsed_ms=(time.perf_counter() - started_at) * 1000,
            source_counts=source_counts,
            skipped_sources=tuple(skipped_sources),
            source_errors=source_errors,
        )
        logger.info(
            "user_context_snapshot built readiness=%s elapsed_ms=%.1f "
            "source_counts=%s skipped_sources=%s source_errors=%s",
            snapshot.interaction_readiness,
            trace.elapsed_ms,
            trace.source_counts,
            trace.skipped_sources,
            trace.source_errors,
        )
        return UserContextSnapshotBuildResult(snapshot=snapshot, trace=trace)

    async def _load_latest_presence(
        self,
        *,
        skipped_sources: list[str],
        source_errors: dict[str, str],
    ) -> HumanPresenceObservation | None:
        if self.presence_store is None:
            skipped_sources.append("presence")
            return None
        try:
            latest = await self.presence_store.fetch_latest(limit=1)
        except Exception as exc:
            source_errors["presence"] = type(exc).__name__
            return None
        return latest[0] if latest else None

    async def _load_latest_activity(
        self,
        *,
        skipped_sources: list[str],
        source_errors: dict[str, str],
    ) -> HumanActivityObservation | None:
        if self.activity_store is None:
            skipped_sources.append("activity")
            return None
        try:
            latest = await self.activity_store.fetch_latest(limit=1)
        except Exception as exc:
            source_errors["activity"] = type(exc).__name__
            return None
        return latest[0] if latest else None

    async def _load_latest_screen(
        self,
        *,
        skipped_sources: list[str],
        source_errors: dict[str, str],
    ) -> ScreenActivityObservation | None:
        if self.screen_store is None:
            skipped_sources.append("screen")
            return None
        try:
            latest = await self.screen_store.fetch_latest(limit=1)
        except Exception as exc:
            source_errors["screen"] = type(exc).__name__
            return None
        return latest[0] if latest else None

    async def _load_calendar_events(
        self,
        *,
        now: datetime,
        skipped_sources: list[str],
        source_errors: dict[str, str],
    ) -> list[CalendarEvent]:
        if self.calendar_store is None:
            skipped_sources.append("calendar")
            return []
        try:
            return await self.calendar_store.read_context_events(
                now=now,
                days_before=0,
                days_ahead=1,
                limit=3,
            )
        except Exception as exc:
            source_errors["calendar"] = type(exc).__name__
            return []

    async def _load_world_records(
        self,
        *,
        skipped_sources: list[str],
        source_errors: dict[str, str],
    ) -> tuple[WorldObservationInterpretationRecord, ...]:
        if self.world_store is None:
            skipped_sources.append("world")
            return ()
        try:
            return await self.world_store.fetch_candidate_interpretations(limit=3)
        except Exception as exc:
            source_errors["world"] = type(exc).__name__
            return ()


def decide_interaction_readiness(
    *,
    present: bool | None,
    activity_label: str | None,
    screen_activity_label: str | None,
) -> InteractionReadiness:
    if present is False:
        return "away"
    activity = (activity_label or "").lower()
    screen = (screen_activity_label or "").lower()
    if any(token in activity for token in ("guitar", "playing", "sleep", "music")):
        return "do_not_disturb"
    if any(token in screen for token in ("video", "movie", "watching")):
        return "do_not_disturb"
    if any(token in screen for token in ("debug", "test", "error", "pytest", "traceback")):
        return "needs_help_maybe"
    if activity in {"idle", "away"}:
        return "chat_ok" if present is True else "low_intrusion_ok"
    if present is True:
        return "low_intrusion_ok"
    return "low_intrusion_ok"


def _calendar_summary(
    events: list[CalendarEvent],
    *,
    now: datetime,
) -> str | None:
    del now
    if not events:
        return None
    parts = []
    for event in events[:3]:
        time_text = "終日" if event.all_day else event.start_time.strftime("%H:%M")
        parts.append(f"{time_text} {event.summary}")
    return "; ".join(parts)


def _world_summary(
    records: tuple[WorldObservationInterpretationRecord, ...],
) -> str | None:
    if not records:
        return None
    parts: list[str] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        key = (record.title, record.interpretation_text)
        if key in seen:
            continue
        seen.add(key)
        parts.append(f"{record.title}: {record.interpretation_text}")
        if len(parts) >= 3:
            break
    return "; ".join(parts) if parts else None


def _source_frame_ids(
    presence: HumanPresenceObservation | None,
    activity: HumanActivityObservation | None,
    screen: ScreenActivityObservation | None,
) -> tuple[UUID, ...]:
    ids = []
    for observation in (presence, activity, screen):
        if observation is not None and observation.frame_id not in ids:
            ids.append(observation.frame_id)
    return tuple(ids)


def _source_observation_ids(
    presence: HumanPresenceObservation | None,
    activity: HumanActivityObservation | None,
    screen: ScreenActivityObservation | None,
) -> tuple[UUID, ...]:
    ids = []
    for observation in (presence, activity, screen):
        if observation is not None and observation.id is not None:
            ids.append(observation.id)
    return tuple(ids)


def _user_activity_summary(
    *,
    present: bool | None,
    activity_label: str | None,
    screen_activity_label: str | None,
) -> str:
    parts = []
    if present is not None:
        parts.append("present" if present else "away")
    if activity_label:
        parts.append(f"activity={activity_label}")
    if screen_activity_label:
        parts.append(f"screen={screen_activity_label}")
    return "; ".join(parts) if parts else "unknown"


def _context_summary(
    *,
    user_activity_summary: str,
    calendar_summary: str | None,
    world_summary: str | None,
    readiness: InteractionReadiness,
) -> str:
    parts = [f"user={user_activity_summary}", f"readiness={readiness}"]
    if calendar_summary:
        parts.append(f"calendar={calendar_summary}")
    if world_summary:
        parts.append(f"world={world_summary}")
    return " | ".join(parts)


def _confidence(
    presence: HumanPresenceObservation | None,
    activity: HumanActivityObservation | None,
    screen: ScreenActivityObservation | None,
) -> float:
    values = [
        observation.confidence
        for observation in (presence, activity, screen)
        if observation is not None
    ]
    if not values:
        return 0.0
    return min(values)
