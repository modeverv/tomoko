from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import numpy as np
import pytest

from server.edge.pipeline.vad import VADProcessor
from server.session import TomoroSession
from server.shared.models import ConnectedOutputState, SessionEvent, ThinkingEvent, Transcript
from server.shared.task_ledger import (
    InMemoryTaskLedgerStore,
    TaskLedgerCommandRunner,
    TaskLedgerIntent,
    TaskLedgerIntentDetector,
    TaskLedgerUpdateResult,
    make_task_id,
)


class QuietVAD:
    def process_chunk(self, chunk: np.ndarray) -> float:
        del chunk
        return 0.0


class FakeStructuredBackend:
    name = "fake_task_ledger"

    def __init__(self, payload: str) -> None:
        self.payload = payload

    async def chat_stream_structured(self, *_args, **_kwargs):
        yield self.payload


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


class RecordingContextBuilder:
    def __init__(self) -> None:
        self.invalidated: list[tuple[str, object]] = []

    def invalidate_session_cache_source(
        self,
        source: str,
        *,
        session_id: object = None,
    ) -> None:
        self.invalidated.append((source, session_id))


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


@pytest.mark.unit
def test_task_ledger_intent_detector_extracts_clean_create_and_complete_titles() -> None:
    detector = TaskLedgerIntentDetector()

    create = detector.detect("ともこ、ログ確認をタスクにして")
    complete = detector.detect("ログ確認は終わった")
    unsupported = detector.detect("このタスクを変更して")

    assert create == TaskLedgerIntent(
        kind="create",
        raw_text="ともこ、ログ確認をタスクにして",
        title="ログ確認",
        reason="create_cue",
    )
    assert complete is not None
    assert complete.kind == "complete"
    assert complete.title == "ログ確認"
    assert unsupported is not None
    assert unsupported.kind == "unsupported"


@pytest.mark.unit
async def test_task_ledger_runner_creates_and_completes_deterministic_match() -> None:
    store = InMemoryTaskLedgerStore()
    runner = TaskLedgerCommandRunner(store=store)

    created = await runner.run(
        TaskLedgerIntent(
            kind="create",
            raw_text="ログ確認をタスクにして",
            title="ログ確認",
        )
    )
    completed = await runner.run(
        TaskLedgerIntent(
            kind="complete",
            raw_text="ログ確認は終わった",
            title="ログ確認",
        )
    )

    assert created.status == "created"
    assert created.task_id == make_task_id("ログ確認")
    assert completed.status == "completed"
    assert completed.task_id == created.task_id
    assert await store.read_active_tasks(limit=10) == []


@pytest.mark.unit
async def test_task_ledger_runner_uses_structured_completion_when_rule_match_is_unclear() -> None:
    store = InMemoryTaskLedgerStore()
    await store.upsert(task_id="task-log", title="ログ確認", priority=20)
    await store.upsert(task_id="task-ui", title="UI確認", priority=10)
    backend = FakeStructuredBackend(
        '{"decision":"complete","candidates":[{"task_id":"task-ui","confidence":0.88,'
        '"reason":"UI確認の完了発話"}],"reason":"single confident match"}'
    )
    runner = TaskLedgerCommandRunner(store=store, backend_provider=lambda: backend)

    result = await runner.run(
        TaskLedgerIntent(
            kind="complete",
            raw_text="さっきの画面の確認終わった",
            title="さっきの画面の確認",
        )
    )

    assert result.status == "completed"
    assert result.task_id == "task-ui"
    assert [task.task_id for task in await store.read_active_tasks(limit=10)] == [
        "task-log"
    ]


@pytest.mark.unit
async def test_task_ledger_runner_requires_single_confident_valid_completion_candidate() -> None:
    store = InMemoryTaskLedgerStore()
    await store.upsert(task_id="task-log", title="ログ確認")
    backend = FakeStructuredBackend(
        '{"decision":"complete","candidates":[{"task_id":"task-log","confidence":0.4,'
        '"reason":"weak"}],"reason":"low confidence"}'
    )
    runner = TaskLedgerCommandRunner(store=store, backend_provider=lambda: backend)

    result = await runner.run(
        TaskLedgerIntent(
            kind="complete",
            raw_text="さっきの件終わったかも",
            title="さっきの件",
        )
    )

    assert result.status == "needs_confirmation"
    assert [task.task_id for task in await store.read_active_tasks(limit=10)] == [
        "task-log"
    ]


@pytest.mark.unit
async def test_task_ledger_runner_rejects_update_cancel_without_row_change() -> None:
    store = InMemoryTaskLedgerStore()
    await store.upsert(task_id="task-log", title="ログ確認")
    runner = TaskLedgerCommandRunner(store=store)

    result = await runner.run(
        TaskLedgerIntent(
            kind="unsupported",
            raw_text="ログ確認タスクを変更して",
            title="ログ確認",
        )
    )

    assert result.status == "unsupported"
    assert [task.task_id for task in await store.read_active_tasks(limit=10)] == [
        "task-log"
    ]


@pytest.mark.unit
async def test_task_ledger_requested_emits_submit_command() -> None:
    session = _session()
    intent = TaskLedgerIntent(
        kind="create",
        raw_text="ログ確認をタスクにして",
        title="ログ確認",
    )

    result = await session.post_event(
        SessionEvent(type="task_ledger_requested", payload={"intent": intent})
    )

    assert result.emissions[0].type == "task_ledger_request_accepted"
    assert result.emissions[0].payload["operation"] == "create"
    assert [command.type for command in result.commands] == ["submit_task_ledger_update"]
    assert result.commands[0].payload["intent"] == intent


@pytest.mark.unit
async def test_voice_task_ledger_request_dispatches_background_command_and_ack_reply() -> None:
    events: list[dict[str, object]] = []
    thinking_mode = FakeThinkingMode()
    store = InMemoryTaskLedgerStore()
    session = _session(
        events,
        router=FakeRouter(),
        thinking_mode=thinking_mode,
    )
    runner = TaskLedgerCommandRunner(store=store, session=session)
    session.set_task_ledger_transition_handler(runner.run_result)

    await session.process_transcript(
        _transcript("ともこ、ログ確認をタスクにして"),
        reset_audio_input=True,
    )
    await _wait_for_event(events, "task_ledger_update_recorded")
    await session._wait_for_reply_task()

    assert any(event["type"] == "task_ledger_request_accepted" for event in events)
    assert any(event["type"] == "task_ledger_update_recorded" for event in events)
    assert thinking_mode.response_directives
    assert "タスクとして受け取った" in thinking_mode.response_directives[0]
    assert [task.title for task in await store.read_active_tasks(limit=10)] == [
        "ログ確認"
    ]


@pytest.mark.unit
async def test_task_ledger_update_recorded_invalidates_context_cache() -> None:
    context_builder = RecordingContextBuilder()
    session = _session(context_snapshot_builder=context_builder)
    session.active_conversation_session_id = uuid4()

    result = await session.post_event(
        SessionEvent(
            type="task_ledger_update_finished",
            payload={
                "request_id": "task-ledger-1",
                "result": TaskLedgerUpdateResult(
                    status="created",
                    operation="create",
                    task_id="task-1",
                    title="ログ確認",
                ),
            },
        )
    )

    assert result.emissions[0].type == "task_ledger_update_recorded"
    assert context_builder.invalidated == [
        ("task_ledger", session.active_conversation_session_id)
    ]
