from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

import psycopg

from server.shared.models import SessionEvent, TimerAlarmEntry, TransitionResult

logger = logging.getLogger(__name__)

_JST = ZoneInfo("Asia/Tokyo")

TimerAlarmKind = Literal["timer", "alarm"]
TimerAlarmState = Literal["scheduled", "due", "notified", "cancelled", "failed"]
TimerAlarmIntentKind = Literal["create_timer", "create_alarm", "unsupported"]
TimerAlarmCreateStatus = Literal["created", "skipped", "unsupported"]


class TimerAlarmStore(Protocol):
    async def create(
        self,
        *,
        entry_id: str,
        kind: TimerAlarmKind,
        label: str,
        due_at: datetime,
        source: str = "voice",
    ) -> None: ...

    async def claim_due(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int = 10,
    ) -> list[TimerAlarmEntry]: ...

    async def mark_notified(self, *, entry_id: str) -> bool: ...

    async def mark_failed(self, *, entry_id: str, reason: str) -> bool: ...

    async def cancel(self, *, entry_id: str) -> bool: ...


@dataclass(frozen=True)
class TimerAlarmIntent:
    kind: TimerAlarmIntentKind
    raw_text: str
    label: str = ""
    duration_seconds: int | None = None
    target_hour: int | None = None
    target_minute: int | None = None
    day_offset: int | None = None
    reason: str = ""


@dataclass(frozen=True)
class TimerAlarmCreateResult:
    status: TimerAlarmCreateStatus
    kind: TimerAlarmIntentKind
    entry_id: str | None = None
    label: str = ""
    due_at: datetime | None = None
    reason: str = ""


class TimerAlarmIntentDetector:
    def detect(self, text: str) -> TimerAlarmIntent | None:
        normalized = _normalize_text(text)
        if not normalized:
            return None
        if _has_unsupported_cue(normalized):
            return TimerAlarmIntent(
                kind="unsupported",
                raw_text=text,
                reason="recurring_or_complex_not_supported",
            )
        timer_intent = _try_detect_timer(text, normalized)
        if timer_intent is not None:
            return timer_intent
        alarm_intent = _try_detect_alarm(text, normalized)
        if alarm_intent is not None:
            return alarm_intent
        return None


class TimerAlarmCommandRunner:
    def __init__(
        self,
        *,
        store: TimerAlarmStore | None,
        session: Any | None = None,
    ) -> None:
        self.store = store
        self.session = session

    async def run_result(self, result: TransitionResult) -> None:
        for command in result.commands:
            if command.type != "submit_timer_alarm_create":
                continue
            intent = command.payload.get("intent")
            if not isinstance(intent, TimerAlarmIntent):
                create_result = TimerAlarmCreateResult(
                    status="skipped",
                    kind="unsupported",
                    reason="invalid_intent",
                )
            else:
                create_result = await self.run(intent)
            if self.session is None:
                continue
            transition = await self.session.post_event(
                SessionEvent(
                    type="timer_alarm_create_finished",
                    payload={
                        "request_id": command.payload.get("request_id"),
                        "result": create_result,
                    },
                )
            )
            await self.session.send_transition_emissions(transition)
            await self.session._run_internal_commands(transition.commands)

    async def run(self, intent: TimerAlarmIntent) -> TimerAlarmCreateResult:
        if self.store is None:
            return TimerAlarmCreateResult(
                status="skipped",
                kind=intent.kind,
                label=intent.label,
                reason="missing_timer_alarm_store",
            )
        if intent.kind == "unsupported":
            return TimerAlarmCreateResult(
                status="unsupported",
                kind=intent.kind,
                label=intent.label,
                reason=intent.reason or "unsupported_operation",
            )
        if intent.kind == "create_timer":
            return await self._create_timer(intent)
        if intent.kind == "create_alarm":
            return await self._create_alarm(intent)
        return TimerAlarmCreateResult(
            status="skipped",
            kind=intent.kind,
            reason="unknown_intent_kind",
        )

    async def _create_timer(self, intent: TimerAlarmIntent) -> TimerAlarmCreateResult:
        if intent.duration_seconds is None or intent.duration_seconds <= 0:
            return TimerAlarmCreateResult(
                status="skipped",
                kind="create_timer",
                reason="invalid_duration",
            )
        now = datetime.now(UTC)
        due_at = now + timedelta(seconds=intent.duration_seconds)
        label = intent.label or f"{intent.duration_seconds}秒タイマー"
        entry_id = _make_entry_id("timer", label, now)
        await self.store.create(
            entry_id=entry_id,
            kind="timer",
            label=label,
            due_at=due_at,
            source="voice",
        )
        return TimerAlarmCreateResult(
            status="created",
            kind="create_timer",
            entry_id=entry_id,
            label=label,
            due_at=due_at,
            reason="timer_created_from_transcript",
        )

    async def _create_alarm(self, intent: TimerAlarmIntent) -> TimerAlarmCreateResult:
        if intent.target_hour is None:
            return TimerAlarmCreateResult(
                status="skipped",
                kind="create_alarm",
                reason="invalid_alarm_time",
            )
        now_jst = datetime.now(_JST)
        day_offset = intent.day_offset if intent.day_offset is not None else 0
        alarm_dt_jst = now_jst.replace(
            hour=intent.target_hour,
            minute=intent.target_minute or 0,
            second=0,
            microsecond=0,
        ) + timedelta(days=day_offset)
        if day_offset == 0 and alarm_dt_jst <= now_jst:
            alarm_dt_jst += timedelta(days=1)
        due_at = alarm_dt_jst.astimezone(UTC)
        label = intent.label or f"{intent.target_hour}時アラーム"
        entry_id = _make_entry_id("alarm", label, datetime.now(UTC))
        await self.store.create(
            entry_id=entry_id,
            kind="alarm",
            label=label,
            due_at=due_at,
            source="voice",
        )
        return TimerAlarmCreateResult(
            status="created",
            kind="create_alarm",
            entry_id=entry_id,
            label=label,
            due_at=due_at,
            reason="alarm_created_from_transcript",
        )


class TimerAlarmWorker:
    """Polls and claims due timer/alarm rows. Run as a separate process loop."""

    def __init__(self, *, store: TimerAlarmStore, worker_id: str) -> None:
        self.store = store
        self.worker_id = worker_id

    async def poll_and_claim(self, *, limit: int = 10) -> list[TimerAlarmEntry]:
        now = datetime.now(UTC)
        return await self.store.claim_due(worker_id=self.worker_id, now=now, limit=limit)

    async def mark_notified(self, *, entry_id: str) -> bool:
        return await self.store.mark_notified(entry_id=entry_id)

    async def mark_failed(self, *, entry_id: str, reason: str) -> bool:
        return await self.store.mark_failed(entry_id=entry_id, reason=reason)


class InMemoryTimerAlarmStore:
    def __init__(self) -> None:
        self._rows: dict[str, _StoredRow] = {}

    async def create(
        self,
        *,
        entry_id: str,
        kind: TimerAlarmKind,
        label: str,
        due_at: datetime,
        source: str = "voice",
    ) -> None:
        now = datetime.now(UTC)
        self._rows[entry_id] = _StoredRow(
            entry_id=entry_id,
            kind=kind,
            label=label,
            status="scheduled",
            due_at=due_at,
            source=source,
            created_at=now,
            updated_at=now,
        )

    async def claim_due(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int = 10,
    ) -> list[TimerAlarmEntry]:
        claimed: list[TimerAlarmEntry] = []
        for row in list(self._rows.values()):
            if len(claimed) >= limit:
                break
            if row.status != "scheduled" or row.due_at > now:
                continue
            updated = _StoredRow(
                entry_id=row.entry_id,
                kind=row.kind,
                label=row.label,
                status="due",
                due_at=row.due_at,
                source=row.source,
                created_at=row.created_at,
                updated_at=datetime.now(UTC),
                claimed_worker_id=worker_id,
                claimed_at=datetime.now(UTC),
            )
            self._rows[row.entry_id] = updated
            claimed.append(updated.to_entry())
        return claimed

    async def mark_notified(self, *, entry_id: str) -> bool:
        row = self._rows.get(entry_id)
        if row is None or row.status != "due":
            return False
        now = datetime.now(UTC)
        self._rows[entry_id] = _StoredRow(
            entry_id=row.entry_id,
            kind=row.kind,
            label=row.label,
            status="notified",
            due_at=row.due_at,
            source=row.source,
            created_at=row.created_at,
            updated_at=now,
            notified_at=now,
            claimed_worker_id=row.claimed_worker_id,
            claimed_at=row.claimed_at,
        )
        return True

    async def mark_failed(self, *, entry_id: str, reason: str) -> bool:
        row = self._rows.get(entry_id)
        if row is None or row.status not in {"scheduled", "due"}:
            return False
        now = datetime.now(UTC)
        self._rows[entry_id] = _StoredRow(
            entry_id=row.entry_id,
            kind=row.kind,
            label=row.label,
            status="failed",
            due_at=row.due_at,
            source=row.source,
            created_at=row.created_at,
            updated_at=now,
            failed_at=now,
            failure_reason=reason,
            claimed_worker_id=row.claimed_worker_id,
            claimed_at=row.claimed_at,
        )
        return True

    async def cancel(self, *, entry_id: str) -> bool:
        row = self._rows.get(entry_id)
        if row is None or row.status not in {"scheduled", "due"}:
            return False
        now = datetime.now(UTC)
        self._rows[entry_id] = _StoredRow(
            entry_id=row.entry_id,
            kind=row.kind,
            label=row.label,
            status="cancelled",
            due_at=row.due_at,
            source=row.source,
            created_at=row.created_at,
            updated_at=now,
            cancelled_at=now,
            claimed_worker_id=row.claimed_worker_id,
            claimed_at=row.claimed_at,
        )
        return True


class PostgresTimerAlarmStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def create(
        self,
        *,
        entry_id: str,
        kind: TimerAlarmKind,
        label: str,
        due_at: datetime,
        source: str = "voice",
    ) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO timer_alarm_entries (
                        id, kind, label, status, due_at, source, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, 'scheduled', %s, %s, now(), now())
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (entry_id, kind, label, due_at, source),
                )

    async def claim_due(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int = 10,
    ) -> list[TimerAlarmEntry]:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE timer_alarm_entries
                    SET
                        status = 'due',
                        claimed_worker_id = %s,
                        claimed_at = now(),
                        updated_at = now()
                    WHERE id IN (
                        SELECT id FROM timer_alarm_entries
                        WHERE status = 'scheduled' AND due_at <= %s
                        ORDER BY due_at
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING id, kind, label, status, due_at, source, created_at, notified_at
                    """,
                    (worker_id, now, limit),
                )
                rows = await cur.fetchall()
        return [
            TimerAlarmEntry(
                entry_id=row[0],
                kind=row[1],
                label=row[2],
                status=row[3],
                due_at=row[4],
                source=row[5],
                created_at=row[6],
                notified_at=row[7],
            )
            for row in rows
        ]

    async def mark_notified(self, *, entry_id: str) -> bool:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE timer_alarm_entries
                    SET status = 'notified', notified_at = now(), updated_at = now()
                    WHERE id = %s AND status = 'due'
                    """,
                    (entry_id,),
                )
                return cur.rowcount == 1

    async def mark_failed(self, *, entry_id: str, reason: str) -> bool:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE timer_alarm_entries
                    SET
                        status = 'failed',
                        failed_at = now(),
                        failure_reason = %s,
                        updated_at = now()
                    WHERE id = %s AND status IN ('scheduled', 'due')
                    """,
                    (reason, entry_id),
                )
                return cur.rowcount == 1

    async def cancel(self, *, entry_id: str) -> bool:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE timer_alarm_entries
                    SET status = 'cancelled', cancelled_at = now(), updated_at = now()
                    WHERE id = %s AND status IN ('scheduled', 'due')
                    """,
                    (entry_id,),
                )
                return cur.rowcount == 1


@dataclass
class _StoredRow:
    entry_id: str
    kind: str
    label: str
    status: str
    due_at: datetime
    source: str
    created_at: datetime
    updated_at: datetime
    notified_at: datetime | None = None
    cancelled_at: datetime | None = None
    failed_at: datetime | None = None
    claimed_worker_id: str | None = None
    claimed_at: datetime | None = None
    failure_reason: str = ""

    def to_entry(self) -> TimerAlarmEntry:
        return TimerAlarmEntry(
            entry_id=self.entry_id,
            kind=self.kind,  # type: ignore[arg-type]
            label=self.label,
            status=self.status,  # type: ignore[arg-type]
            due_at=self.due_at,
            source=self.source,
            created_at=self.created_at,
            notified_at=self.notified_at,
        )


def _make_entry_id(kind: str, label: str, now: datetime) -> str:
    digest = hashlib.sha1(
        f"{kind}:{label}:{now.isoformat()}".encode()
    ).hexdigest()
    return f"{kind}-{digest[:16]}"


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _has_unsupported_cue(text: str) -> bool:
    cues = (
        "ポモドーロ",
        "毎日",
        "毎時",
        "毎朝",
        "毎晩",
        "繰り返し",
        "くり返し",
        "定期的",
        "連続",
        r"\d+回",
    )
    lowered = text.lower()
    for cue in cues:
        if re.search(cue, lowered):
            return True
    return False


def _try_detect_timer(raw_text: str, normalized: str) -> TimerAlarmIntent | None:
    has_timer_cue = any(
        cue in normalized.lower()
        for cue in (
            "タイマー",
            "timer",
            "後に教えて",
            "後に知らせて",
            "後で教えて",
            "後で知らせて",
            "後に呼んで",
            "後で呼んで",
        )
    )
    duration = _parse_duration_seconds(normalized)
    if duration is None:
        return None
    if not has_timer_cue and not _has_duration_suffix(normalized):
        return None
    label = _make_timer_label(duration)
    return TimerAlarmIntent(
        kind="create_timer",
        raw_text=raw_text,
        label=label,
        duration_seconds=duration,
        reason="timer_cue",
    )


def _has_duration_suffix(text: str) -> bool:
    return bool(re.search(r"\d+\s*(時間|分|秒)\s*後", text))


def _parse_duration_seconds(text: str) -> int | None:
    total = 0
    found = False
    for match in re.finditer(r"(\d+)\s*(時間|分|秒)", text):
        value = int(match.group(1))
        unit = match.group(2)
        if unit == "時間":
            total += value * 3600
        elif unit == "分":
            total += value * 60
        elif unit == "秒":
            total += value
        found = True
    return total if found else None


def _make_timer_label(duration_seconds: int) -> str:
    hours, rem = divmod(duration_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}時間")
    if minutes:
        parts.append(f"{minutes}分")
    if seconds and not hours:
        parts.append(f"{seconds}秒")
    return "".join(parts) + "タイマー" if parts else "タイマー"


def _try_detect_alarm(raw_text: str, normalized: str) -> TimerAlarmIntent | None:
    simple_cues = ("アラーム", "alarm", "時に教えて", "時に知らせて", "時に起こして", "時に呼んで")
    has_alarm_cue = any(cue in normalized.lower() for cue in simple_cues) or bool(
        re.search(r"\d+時.*に(教えて|知らせて|起こして|呼んで)", normalized)
    )
    if not has_alarm_cue:
        return None
    hour, minute = _parse_clock_time(normalized)
    if hour is None:
        return None
    day_offset = _parse_day_offset(normalized)
    label = _make_alarm_label(hour, minute, day_offset)
    return TimerAlarmIntent(
        kind="create_alarm",
        raw_text=raw_text,
        label=label,
        target_hour=hour,
        target_minute=minute,
        day_offset=day_offset,
        reason="alarm_cue",
    )


def _parse_clock_time(text: str) -> tuple[int | None, int | None]:
    match = re.search(r"(\d{1,2})時(?:(\d{1,2})分)?", text)
    if not match:
        return None, None
    hour = int(match.group(1))
    minute = int(match.group(2)) if match.group(2) else 0
    if hour > 23 or minute > 59:
        return None, None
    return hour, minute


def _parse_day_offset(text: str) -> int:
    if "明日" in text or "あした" in text or "あす" in text:
        return 1
    return 0


def _make_alarm_label(hour: int, minute: int | None, day_offset: int) -> str:
    prefix = "明日" if day_offset else "今日"
    if minute:
        return f"{prefix}{hour}時{minute:02d}分アラーム"
    return f"{prefix}{hour}時アラーム"
