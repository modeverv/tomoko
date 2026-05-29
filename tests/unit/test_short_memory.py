from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from server.gateway.context import ContextSnapshotBuilder
from server.session import TomoroSession
from server.session_short_memory import (
    ShortMemoryBuffer,
    format_short_memory_prompt,
    propose_short_memory_notes,
)
from server.shared.models import ShortMemoryNote, ThinkingEvent, ThinkingInput, Transcript


class FakeVADProcessor:
    device_id = "local"
    sample_rate = 16000


class FakeBackend:
    name = "fake"


class FakeRouter:
    async def select(self, role: str, preference: str) -> FakeBackend:
        del role, preference
        return FakeBackend()


class TextReplyThinkingMode:
    def __init__(self, text: str = "了解、短期メモに回すね。") -> None:
        self.text = text
        self.inputs: list[ThinkingInput] = []

    async def think(self, backend: Any, thinking_input: ThinkingInput):
        del backend
        self.inputs.append(thinking_input)
        yield ThinkingEvent(type="text_delta", value=self.text)
        yield ThinkingEvent(type="done", value="")


def _note(text: str, *, created_turn: int = 1, ttl: int = 4) -> ShortMemoryNote:
    return ShortMemoryNote(
        kind="working_context",
        text=text,
        confidence=0.85,
        importance=0.8,
        created_turn=created_turn,
        expires_after_turns=ttl,
        created_at=datetime(2026, 5, 29, 12, 0, tzinfo=UTC),
    )


def _transcript(text: str) -> Transcript:
    return Transcript(
        text=text,
        device_id="local",
        speaker=None,
        audio_level_db=-20.0,
        recorded_at=datetime(2026, 5, 29, 12, 0, tzinfo=UTC),
        is_final=True,
    )


@pytest.mark.unit
def test_short_memory_buffer_appends_notes() -> None:
    buffer = ShortMemoryBuffer(max_notes=5, default_ttl_turns=4)

    added = buffer.append(_note("DB 永続化前に buffer で試す"))

    assert added.text == "DB 永続化前に buffer で試す"
    assert buffer.read_for_prompt(current_turn=1) == [added]


@pytest.mark.unit
def test_short_memory_buffer_drops_oldest_when_max_notes_is_exceeded() -> None:
    buffer = ShortMemoryBuffer(max_notes=2, default_ttl_turns=4)

    buffer.append(_note("first"))
    buffer.append(_note("second"))
    buffer.append(_note("third"))

    assert [note.text for note in buffer.read_for_prompt(current_turn=1)] == [
        "second",
        "third",
    ]


@pytest.mark.unit
def test_short_memory_buffer_expires_by_turn_ttl() -> None:
    buffer = ShortMemoryBuffer(max_notes=5, default_ttl_turns=4)
    buffer.append(_note("短期メモ", created_turn=2, ttl=3))

    expired = buffer.expire_by_turn(current_turn=5)

    assert [note.text for note in expired] == ["短期メモ"]
    assert buffer.read_for_prompt(current_turn=5) == []


@pytest.mark.unit
def test_short_memory_prompt_marks_notes_as_non_permanent_hints() -> None:
    prompt = format_short_memory_prompt([_note("hot path を待たせない")])

    assert "SHORT WORKING MEMORY" in prompt
    assert "not permanent facts" in prompt
    assert "- hot path を待たせない" in prompt


@pytest.mark.unit
def test_short_memory_snapshot_is_ui_safe() -> None:
    buffer = ShortMemoryBuffer(max_notes=5, default_ttl_turns=4)
    buffer.append(_note("UI に表示する", created_turn=3, ttl=4))

    snapshot = buffer.snapshot_for_ui(current_turn=4)

    assert snapshot == {
        "current_turn": 4,
        "notes": [
            {
                "id": snapshot["notes"][0]["id"],
                "kind": "working_context",
                "text": "UI に表示する",
                "confidence": 0.85,
                "importance": 0.8,
                "created_turn": 3,
                "expires_after_turns": 4,
                "remaining_turns": 3,
                "status": "accepted",
            }
        ],
    }


@pytest.mark.unit
def test_heuristic_proposal_keeps_working_context_only() -> None:
    result = propose_short_memory_notes(
        user_text="トモコ、DB 永続化はまだしないで、short memory buffer だけで試したい",
        reply_text="了解、まず揮発 buffer だけにするね。",
        current_turn=1,
        default_ttl_turns=4,
    )

    assert len(result.proposals) == 1
    assert result.proposals[0].kind == "working_context"
    assert "DB 永続化" in result.proposals[0].text
    assert result.proposals[0].expires_after_turns == 4


@pytest.mark.unit
async def test_session_runs_short_memory_extraction_after_reply_done() -> None:
    events: list[dict[str, object]] = []
    mode = TextReplyThinkingMode()
    session = TomoroSession(
        vad_processor=FakeVADProcessor(),  # type: ignore[arg-type]
        send_event=events.append,
        router=FakeRouter(),  # type: ignore[arg-type]
        thinking_mode=mode,  # type: ignore[arg-type]
        context_snapshot_builder=ContextSnapshotBuilder(),
    )

    await session._reply_to(
        _transcript("トモコ、DB 永続化はまだしないで short memory buffer で試したい")
    )
    await session._wait_for_short_memory_extraction_tasks()

    event_types = [event["type"] for event in events]
    assert event_types.index("reply_done") < event_types.index(
        "short_memory_extraction"
    )
    assert any(
        event["type"] == "short_memory_snapshot" and event["notes"]
        for event in events
    )


@pytest.mark.unit
async def test_session_passes_short_memory_to_next_turn_only() -> None:
    mode = TextReplyThinkingMode()
    session = TomoroSession(
        vad_processor=FakeVADProcessor(),  # type: ignore[arg-type]
        send_event=lambda event: None,
        router=FakeRouter(),  # type: ignore[arg-type]
        thinking_mode=mode,  # type: ignore[arg-type]
        context_snapshot_builder=ContextSnapshotBuilder(),
    )

    await session._reply_to(
        _transcript("トモコ、DB 永続化はまだしないで short memory buffer で試したい")
    )
    await session._wait_for_short_memory_extraction_tasks()
    await session._reply_to(_transcript("トモコ、さっきの続き"))
    await session._wait_for_short_memory_extraction_tasks()

    assert mode.inputs[0].short_memory_notes == []
    assert len(mode.inputs[1].short_memory_notes) == 1
    assert "DB 永続化" in mode.inputs[1].short_memory_notes[0].text
