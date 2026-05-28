from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import numpy as np
import pytest

from server.edge.participation.wake_word import WakeWordJudge
from server.edge.pipeline.vad import VADProcessor
from server.gateway.thinking.base import ThinkingMode
from server.session import TomoroSession
from server.shared.inference.backends.base import InferenceBackend
from server.shared.models import (
    AttentionMode,
    ConnectedOutputState,
    ConversationTurn,
    ParticipationMode,
    SessionEvent,
    SpeechSegment,
    ThinkingEvent,
    ThinkingInput,
    Transcript,
)


class SequenceVAD:
    def __init__(self, scores: list[float]) -> None:
        self.scores = scores
        self.index = 0

    def process_chunk(self, chunk: np.ndarray) -> float:
        score = self.scores[self.index]
        self.index += 1
        return score


class QueueTranscriber:
    def __init__(self, texts: list[str]) -> None:
        self.texts = texts

    async def transcribe(self, segment: SpeechSegment) -> Transcript:
        return Transcript(
            text=self.texts.pop(0),
            device_id=segment.device_id,
            speaker=None,
            audio_level_db=-20.0,
            recorded_at=datetime.now(UTC),
            is_final=True,
        )


class InMemoryAmbientLogWriter:
    async def write(
        self,
        transcript: Transcript,
        *,
        tomoko_participated: bool,
        attention_mode: AttentionMode,
        attended: bool,
        participation_mode: ParticipationMode,
    ) -> None:
        del transcript, tomoko_participated, attention_mode, attended, participation_mode


class InMemoryConversationSessionStore:
    def __init__(self) -> None:
        self.created: list[tuple[UUID, str, str]] = []
        self.closed: list[tuple[UUID, str]] = []

    async def create_session(self, *, device_id: str, start_reason: str) -> UUID:
        session_id = uuid4()
        self.created.append((session_id, device_id, start_reason))
        return session_id

    async def close_session(self, session_id: UUID, *, end_reason: str) -> None:
        self.closed.append((session_id, end_reason))


class InMemoryConversationLogWriter:
    def __init__(
        self,
        *,
        same_session_history: list[ConversationTurn] | None = None,
        recent_history: list[ConversationTurn] | None = None,
    ) -> None:
        self.same_session_history = same_session_history or []
        self.recent_history = recent_history or []
        self.user_turns: list[tuple[Transcript, ParticipationMode, UUID | None]] = []
        self.tomoko_turns: list[tuple[str, str, str, UUID | None]] = []

    async def write_user_turn(
        self,
        transcript: Transcript,
        *,
        participation_mode: ParticipationMode,
        conversation_session_id: UUID | None = None,
    ) -> UUID:
        self.user_turns.append((transcript, participation_mode, conversation_session_id))
        turn = ConversationTurn(
            speaker="user",
            text=transcript.text,
            timestamp=transcript.recorded_at,
        )
        if conversation_session_id is not None:
            self.same_session_history.append(turn)
        self.recent_history.append(turn)
        return uuid4()

    async def write_tomoko_turn(
        self,
        *,
        text: str,
        emotion: str,
        device_id: str,
        status: str = "completed",
        conversation_session_id: UUID | None = None,
    ) -> UUID:
        del device_id
        self.tomoko_turns.append((text, emotion, status, conversation_session_id))
        turn = ConversationTurn(
            speaker="tomoko",
            text=text,
            timestamp=datetime.now(UTC),
            emotion=emotion,
        )
        if conversation_session_id is not None:
            self.same_session_history.append(turn)
        self.recent_history.append(turn)
        return uuid4()

    async def read_recent_turns(self, *, limit: int) -> list[ConversationTurn]:
        return self.recent_history[-limit:]

    async def read_recent_turns_for_session(
        self,
        *,
        conversation_session_id: UUID,
        limit: int,
    ) -> list[ConversationTurn]:
        del conversation_session_id
        return self.same_session_history[-limit:]


class FakeBackend(InferenceBackend):
    name = "fake"
    privacy_allowed = True

    async def chat_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
    ) -> AsyncGenerator[str, None]:
        del system_prompt, messages
        yield "EMOTION:happy\n"
        yield "覚えてるよ。"


class FakeRouter:
    async def select(self, role: str, preference: str = "privacy") -> InferenceBackend:
        del role, preference
        return FakeBackend()


class RecordingMode(ThinkingMode):
    def __init__(self) -> None:
        self.inputs: list[ThinkingInput] = []

    async def think(
        self,
        backend: InferenceBackend,
        thinking_input: ThinkingInput,
    ) -> AsyncGenerator[ThinkingEvent, None]:
        del backend
        self.inputs.append(thinking_input)
        yield ThinkingEvent(type="text_delta", value="覚えてるよ。")
        yield ThinkingEvent(type="done", value="")


async def run_one_finished_speech(session: TomoroSession) -> None:
    for _ in range(14):
        await session.process_audio_chunk(np.ones(512, dtype=np.float32).tobytes())


@pytest.mark.unit
async def test_participating_speech_creates_one_active_conversation_session() -> None:
    sessions = InMemoryConversationSessionStore()
    conversation_logs = InMemoryConversationLogWriter()
    events: list[dict[str, object]] = []
    session = TomoroSession(
        vad_processor=VADProcessor(
            vad=SequenceVAD(([0.9] + [0.1] * 13) * 2),
            silence_ms=400,
        ),
        send_event=events.append,
        transcriber=QueueTranscriber(["トモコ、聞こえる？", "さっきの続き"]),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=InMemoryAmbientLogWriter(),
        conversation_log_writer=conversation_logs,  # type: ignore[arg-type]
        conversation_session_store=sessions,  # type: ignore[arg-type]
        router=FakeRouter(),  # type: ignore[arg-type]
        thinking_mode=RecordingMode(),
    )

    await run_one_finished_speech(session)
    first_session_id = session.active_conversation_session_id
    await run_one_finished_speech(session)

    assert first_session_id is not None
    assert sessions.created == [(first_session_id, "local", "wake_word")]
    assert [turn[2] for turn in conversation_logs.user_turns] == [
        first_session_id,
        first_session_id,
    ]
    transcript_events = [
        event for event in events if event["type"] == "transcript_final"
    ]
    assert transcript_events[0]["conversation_session_id"] == str(first_session_id)
    assert transcript_events[0]["participation_mode"] == "called"
    assert transcript_events[1]["conversation_session_id"] == str(first_session_id)
    assert transcript_events[1]["participation_mode"] == "invited"


@pytest.mark.unit
async def test_tomoko_turn_is_saved_with_active_conversation_session() -> None:
    sessions = InMemoryConversationSessionStore()
    conversation_logs = InMemoryConversationLogWriter()
    mode = RecordingMode()
    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.9] + [0.1] * 13), silence_ms=400),
        send_event=lambda event: None,
        transcriber=QueueTranscriber(["トモコ、聞こえる？"]),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=InMemoryAmbientLogWriter(),
        conversation_log_writer=conversation_logs,  # type: ignore[arg-type]
        conversation_session_store=sessions,  # type: ignore[arg-type]
        router=FakeRouter(),  # type: ignore[arg-type]
        thinking_mode=mode,
    )

    await run_one_finished_speech(session)
    await session._wait_for_reply_task()

    active_session_id = session.active_conversation_session_id
    assert active_session_id is not None
    assert conversation_logs.tomoko_turns == [
        ("覚えてるよ。", "neutral", "completed", active_session_id)
    ]


@pytest.mark.unit
async def test_cooldown_to_ambient_closes_active_session_as_pending_summary() -> None:
    sessions = InMemoryConversationSessionStore()
    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.0] * 4), silence_ms=400),
        send_event=lambda event: None,
        conversation_session_store=sessions,  # type: ignore[arg-type]
        engaged_timeout_ms=64,
        cooldown_timeout_ms=64,
    )
    session.active_conversation_session_id = uuid4()
    active_session_id = session.active_conversation_session_id
    await session._transition_attention("engaged")

    for _ in range(2):
        await session.process_audio_chunk(np.zeros(512, dtype=np.float32).tobytes())
    for _ in range(2):
        await session.process_audio_chunk(np.zeros(512, dtype=np.float32).tobytes())

    assert session.attention_mode == "ambient"
    assert session.active_conversation_session_id is None
    assert sessions.closed == [(active_session_id, "attention_timeout")]


@pytest.mark.unit
async def test_recent_context_prefers_same_session_then_supplements_recent_turns() -> None:
    old_turn = ConversationTurn(
        speaker="user",
        text="前の会話のカレーの話",
        timestamp=datetime(2026, 5, 24, 8, 0, tzinfo=UTC),
    )
    same_turn = ConversationTurn(
        speaker="tomoko",
        text="今の会話では予定の話をしていたよ。",
        timestamp=datetime(2026, 5, 24, 9, 0, tzinfo=UTC),
        emotion="gentle",
    )
    sessions = InMemoryConversationSessionStore()
    conversation_logs = InMemoryConversationLogWriter(
        same_session_history=[same_turn],
        recent_history=[old_turn, same_turn],
    )
    mode = RecordingMode()
    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.9] + [0.1] * 13), silence_ms=400),
        send_event=lambda event: None,
        transcriber=QueueTranscriber(["トモコ、さっきの続きだけど"]),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=InMemoryAmbientLogWriter(),
        conversation_log_writer=conversation_logs,  # type: ignore[arg-type]
        conversation_session_store=sessions,  # type: ignore[arg-type]
        router=FakeRouter(),  # type: ignore[arg-type]
        thinking_mode=mode,
    )

    await run_one_finished_speech(session)
    await session._wait_for_reply_task()

    assert mode.inputs
    assert [turn.text for turn in mode.inputs[0].context] == [
        "前の会話のカレーの話",
        "今の会話では予定の話をしていたよ。",
    ]


@pytest.mark.unit
async def test_ambient_observer_speech_does_not_create_conversation_session() -> None:
    sessions = InMemoryConversationSessionStore()
    conversation_logs = InMemoryConversationLogWriter()
    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.9] + [0.1] * 13), silence_ms=400),
        send_event=lambda event: None,
        transcriber=QueueTranscriber(["今日いい天気だね"]),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=InMemoryAmbientLogWriter(),
        conversation_log_writer=conversation_logs,  # type: ignore[arg-type]
        conversation_session_store=sessions,  # type: ignore[arg-type]
    )

    await run_one_finished_speech(session)

    assert sessions.created == []
    assert conversation_logs.user_turns == []


@pytest.mark.unit
async def test_client_stop_event_closes_active_conversation_session() -> None:
    sessions = InMemoryConversationSessionStore()
    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.1]), silence_ms=400),
        send_event=lambda event: None,
        conversation_session_store=sessions,  # type: ignore[arg-type]
    )
    active_session_id = await session._ensure_conversation_session(
        device_id="desk",
        start_reason="wake_word",
    )

    await session.apply_client_lifecycle_event(
        SessionEvent(
            type="client_stop_requested",
            payload={"reason": "ui_stop"},
        )
    )

    assert active_session_id is not None
    assert session.active_conversation_session_id is None
    assert sessions.closed == [(active_session_id, "ui_stop")]


@pytest.mark.unit
async def test_client_disconnect_closes_active_session_when_no_output_target_remains() -> None:
    sessions = InMemoryConversationSessionStore()
    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.1]), silence_ms=400),
        send_event=lambda event: None,
        conversation_session_store=sessions,  # type: ignore[arg-type]
        connected_output_state=ConnectedOutputState.single_client(device_id="desk"),
    )
    active_session_id = await session._ensure_conversation_session(
        device_id="desk",
        start_reason="wake_word",
    )

    await session.apply_client_lifecycle_event(
        SessionEvent(
            type="connected_output_state_changed",
            payload={"output_state": ConnectedOutputState.empty()},
        )
    )

    assert active_session_id is not None
    assert session.active_conversation_session_id is None
    assert sessions.closed == [(active_session_id, "client_disconnect")]


@pytest.mark.unit
async def test_output_state_change_keeps_session_open_when_an_output_target_remains() -> None:
    sessions = InMemoryConversationSessionStore()
    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.1]), silence_ms=400),
        send_event=lambda event: None,
        conversation_session_store=sessions,  # type: ignore[arg-type]
        connected_output_state=ConnectedOutputState.single_client(device_id="desk"),
    )
    active_session_id = await session._ensure_conversation_session(
        device_id="desk",
        start_reason="wake_word",
    )

    await session.apply_client_lifecycle_event(
        SessionEvent(
            type="connected_output_state_changed",
            payload={
                "output_state": ConnectedOutputState.single_client(device_id="monitor")
            },
        )
    )

    assert session.active_conversation_session_id == active_session_id
    assert sessions.closed == []
