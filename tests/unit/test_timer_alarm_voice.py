from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from server.edge.pipeline.vad import VADProcessor
from server.session import TomoroSession
from server.shared.models import ConnectedOutputState, SessionEvent, ThinkingEvent, Transcript
from server.shared.timer_alarm import (
    InMemoryTimerAlarmStore,
    TimerAlarmCommandRunner,
    TimerAlarmIntent,
    TimerAlarmIntentDetector,
)


class QuietVAD:
    def process_chunk(self, chunk: np.ndarray) -> float:
        del chunk
        return 0.0


class FakeConversationBackend:
    name = "fake_conversation"
    privacy_allowed = True


class FakeRouter:
    async def select(self, role: str, preference: str = "latency") -> FakeConversationBackend:
        del role, preference
        return FakeConversationBackend()


class FakeThinkingMode:
    def __init__(self) -> None:
        self.response_directives: list[str | None] = []

    async def think(self, backend, thinking_input):
        del backend
        self.response_directives.append(thinking_input.response_directive)
        yield ThinkingEvent(type="emotion", value="thinking")
        yield ThinkingEvent(type="text_delta", value="了解。")
        yield ThinkingEvent(type="done", value="")


def _transcript(text: str) -> Transcript:
    return Transcript(
        text=text,
        device_id="desk",
        speaker=None,
        audio_level_db=-20.0,
        recorded_at=datetime(2026, 6, 2, tzinfo=UTC),
        is_final=True,
    )


def _session(
    events: list[dict[str, object]] | None = None,
    **kwargs,
) -> TomoroSession:
    if events is None:
        events = []
    return TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=events.append,
        connected_output_state=ConnectedOutputState.single_client(device_id="desk"),
        **kwargs,
    )


async def _wait_for_event(
    events: list[dict[str, object]],
    event_type: str,
) -> dict[str, object]:
    for _ in range(20):
        for event in events:
            if event.get("type") == event_type:
                return event
        await asyncio.sleep(0.01)
    raise AssertionError(f"event not observed: {event_type}")


# --- Intent detector tests ---

@pytest.mark.unit
def test_timer_alarm_detector_creates_timer_intent() -> None:
    detector = TimerAlarmIntentDetector()

    result = detector.detect("ともこ、5分後に教えて")

    assert result is not None
    assert result.kind == "create_timer"
    assert result.duration_seconds == 300
    assert "5分" in result.label


@pytest.mark.unit
def test_timer_alarm_detector_creates_timer_with_hours_and_minutes() -> None:
    detector = TimerAlarmIntentDetector()

    result = detector.detect("1時間30分後に知らせて")

    assert result is not None
    assert result.kind == "create_timer"
    assert result.duration_seconds == 5400


@pytest.mark.unit
def test_timer_alarm_detector_creates_alarm_intent() -> None:
    detector = TimerAlarmIntentDetector()

    result = detector.detect("ともこ、明日の9時に起こして")

    assert result is not None
    assert result.kind == "create_alarm"
    assert result.target_hour == 9
    assert result.day_offset == 1


@pytest.mark.unit
def test_timer_alarm_detector_creates_alarm_with_minutes() -> None:
    detector = TimerAlarmIntentDetector()

    result = detector.detect("今日の14時30分に教えて")

    assert result is not None
    assert result.kind == "create_alarm"
    assert result.target_hour == 14
    assert result.target_minute == 30
    assert result.day_offset == 0


@pytest.mark.unit
def test_timer_alarm_detector_rejects_pomodoro() -> None:
    detector = TimerAlarmIntentDetector()

    result = detector.detect("ポモドーロタイマーをセットして")

    assert result is not None
    assert result.kind == "unsupported"


@pytest.mark.unit
def test_timer_alarm_detector_rejects_recurring() -> None:
    detector = TimerAlarmIntentDetector()

    result = detector.detect("毎日9時に起こして")

    assert result is not None
    assert result.kind == "unsupported"


@pytest.mark.unit
def test_timer_alarm_detector_returns_none_for_unrelated_text() -> None:
    detector = TimerAlarmIntentDetector()

    assert detector.detect("今日のランチは何にしようか") is None
    assert detector.detect("ログ確認をタスクにして") is None


# --- InMemory store tests ---

@pytest.mark.unit
async def test_in_memory_store_create_and_claim() -> None:
    store = InMemoryTimerAlarmStore()
    now = datetime.now(UTC)
    due_at = now - timedelta(seconds=1)

    await store.create(
        entry_id="timer-001",
        kind="timer",
        label="5分タイマー",
        due_at=due_at,
    )

    claimed = await store.claim_due(worker_id="worker-1", now=now, limit=10)

    assert len(claimed) == 1
    assert claimed[0].entry_id == "timer-001"
    assert claimed[0].status == "due"
    assert claimed[0].kind == "timer"


@pytest.mark.unit
async def test_in_memory_store_does_not_claim_future_entries() -> None:
    store = InMemoryTimerAlarmStore()
    now = datetime.now(UTC)
    due_at = now + timedelta(minutes=5)

    await store.create(
        entry_id="timer-002",
        kind="timer",
        label="5分タイマー",
        due_at=due_at,
    )

    claimed = await store.claim_due(worker_id="worker-1", now=now, limit=10)

    assert claimed == []


@pytest.mark.unit
async def test_in_memory_store_mark_notified() -> None:
    store = InMemoryTimerAlarmStore()
    now = datetime.now(UTC)

    await store.create(
        entry_id="timer-003",
        kind="timer",
        label="テスト",
        due_at=now - timedelta(seconds=1),
    )
    await store.claim_due(worker_id="w", now=now, limit=10)

    result = await store.mark_notified(entry_id="timer-003")

    assert result is True
    assert store._rows["timer-003"].status == "notified"


@pytest.mark.unit
async def test_in_memory_store_mark_failed() -> None:
    store = InMemoryTimerAlarmStore()
    now = datetime.now(UTC)

    await store.create(
        entry_id="timer-004",
        kind="timer",
        label="テスト",
        due_at=now - timedelta(seconds=1),
    )
    await store.claim_due(worker_id="w", now=now, limit=10)

    result = await store.mark_failed(entry_id="timer-004", reason="gate_blocked")

    assert result is True
    assert store._rows["timer-004"].status == "failed"
    assert store._rows["timer-004"].failure_reason == "gate_blocked"


@pytest.mark.unit
async def test_in_memory_store_cancel() -> None:
    store = InMemoryTimerAlarmStore()

    await store.create(
        entry_id="timer-005",
        kind="alarm",
        label="明日9時アラーム",
        due_at=datetime.now(UTC) + timedelta(hours=1),
    )

    result = await store.cancel(entry_id="timer-005")

    assert result is True
    assert store._rows["timer-005"].status == "cancelled"


# --- CommandRunner tests ---

@pytest.mark.unit
async def test_command_runner_creates_timer_entry() -> None:
    store = InMemoryTimerAlarmStore()
    runner = TimerAlarmCommandRunner(store=store)
    intent = TimerAlarmIntent(
        kind="create_timer",
        raw_text="5分後に教えて",
        label="5分タイマー",
        duration_seconds=300,
    )

    result = await runner.run(intent)

    assert result.status == "created"
    assert result.entry_id is not None
    assert result.due_at is not None
    assert result.due_at > datetime.now(UTC)
    rows = [r for r in store._rows.values() if r.status == "scheduled"]
    assert len(rows) == 1


@pytest.mark.unit
async def test_command_runner_creates_alarm_entry() -> None:
    store = InMemoryTimerAlarmStore()
    runner = TimerAlarmCommandRunner(store=store)
    intent = TimerAlarmIntent(
        kind="create_alarm",
        raw_text="明日の9時に起こして",
        label="明日9時アラーム",
        target_hour=9,
        target_minute=0,
        day_offset=1,
    )

    result = await runner.run(intent)

    assert result.status == "created"
    assert result.entry_id is not None
    assert result.due_at is not None
    rows = [r for r in store._rows.values() if r.status == "scheduled"]
    assert len(rows) == 1


@pytest.mark.unit
async def test_command_runner_skips_unsupported_intent() -> None:
    store = InMemoryTimerAlarmStore()
    runner = TimerAlarmCommandRunner(store=store)
    intent = TimerAlarmIntent(
        kind="unsupported",
        raw_text="ポモドーロタイマーをセットして",
        reason="recurring_or_complex_not_supported",
    )

    result = await runner.run(intent)

    assert result.status == "unsupported"
    assert store._rows == {}


@pytest.mark.unit
async def test_command_runner_skips_when_store_missing() -> None:
    runner = TimerAlarmCommandRunner(store=None)
    intent = TimerAlarmIntent(
        kind="create_timer",
        raw_text="5分後に教えて",
        label="5分タイマー",
        duration_seconds=300,
    )

    result = await runner.run(intent)

    assert result.status == "skipped"
    assert "missing_timer_alarm_store" in result.reason


# --- Session event handling tests ---

@pytest.mark.unit
async def test_timer_alarm_requested_emits_submit_command() -> None:
    session = _session()
    intent = TimerAlarmIntent(
        kind="create_timer",
        raw_text="5分後に教えて",
        label="5分タイマー",
        duration_seconds=300,
    )

    result = await session.post_event(
        SessionEvent(type="timer_alarm_requested", payload={"intent": intent})
    )

    assert result.emissions[0].type == "timer_alarm_request_accepted"
    assert result.emissions[0].payload["operation"] == "create_timer"
    assert [command.type for command in result.commands] == ["submit_timer_alarm_create"]
    assert result.commands[0].payload["intent"] == intent


@pytest.mark.unit
async def test_timer_alarm_unsupported_does_not_emit_command() -> None:
    session = _session()
    intent = TimerAlarmIntent(
        kind="unsupported",
        raw_text="ポモドーロタイマー",
        reason="recurring",
    )

    result = await session.post_event(
        SessionEvent(type="timer_alarm_requested", payload={"intent": intent})
    )

    assert result.emissions[0].type == "timer_alarm_request_unsupported"
    assert result.commands == []


@pytest.mark.unit
async def test_timer_alarm_due_blocked_by_gate_emits_skipped() -> None:
    session = TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=[].append,
        connected_output_state=ConnectedOutputState.empty(),
    )

    result = await session.post_event(
        SessionEvent(
            type="timer_due",
            payload={
                "entry_id": "timer-001",
                "label": "5分タイマー",
                "kind": "timer",
            },
        )
    )

    assert result.emissions[0].type == "timer_alarm_due_skipped"
    assert result.emissions[0].payload["entry_id"] == "timer-001"


@pytest.mark.unit
async def test_timer_alarm_due_speakable_emits_notice_command() -> None:
    events: list[dict[str, object]] = []
    session = _session(events)

    result = await session.post_event(
        SessionEvent(
            type="timer_due",
            payload={
                "entry_id": "timer-001",
                "label": "5分タイマー",
                "kind": "timer",
                "device_id": "desk",
            },
        )
    )

    assert result.emissions[0].type == "timer_alarm_due_speakable"
    assert any(c.type == "start_timer_alarm_due_notice" for c in result.commands)


@pytest.mark.unit
async def test_voice_timer_request_dispatches_background_command_and_ack_reply() -> None:
    events: list[dict[str, object]] = []
    thinking_mode = FakeThinkingMode()
    store = InMemoryTimerAlarmStore()
    session = _session(
        events,
        router=FakeRouter(),
        thinking_mode=thinking_mode,
    )
    runner = TimerAlarmCommandRunner(store=store, session=session)
    session.set_timer_alarm_transition_handler(runner.run_result)

    await session.process_transcript(
        _transcript("ともこ、5分後に教えて"),
        reset_audio_input=True,
    )
    await _wait_for_event(events, "timer_alarm_create_recorded")
    await session._wait_for_reply_task()

    assert any(event["type"] == "timer_alarm_request_accepted" for event in events)
    assert any(event["type"] == "timer_alarm_create_recorded" for event in events)
    assert thinking_mode.response_directives
    assert "タイマー" in thinking_mode.response_directives[0]
    scheduled = [r for r in store._rows.values() if r.status == "scheduled"]
    assert len(scheduled) == 1
