from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import numpy as np
import pytest

from server.edge.participation.wake_word import WakeWordJudge
from server.edge.pipeline.vad import VADProcessor
from server.gateway.thinking.fast import ThinkFastMode
from server.gateway.turn_taking.barge_in import BargeInDetector
from server.session import TomoroSession
from server.shared.inference.backends.base import InferenceBackend
from server.shared.inference.tts.base import TTSBackend
from server.shared.models import (
    AttentionMode,
    AudioChunkOut,
    BargeInContext,
    ParticipationMode,
    PlaybackTelemetry,
    SpeechSegment,
    Transcript,
    TTSInput,
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
    def __init__(self) -> None:
        self.rows: list[tuple[Transcript, bool, AttentionMode, ParticipationMode]] = []

    async def write(
        self,
        transcript: Transcript,
        *,
        tomoko_participated: bool,
        attention_mode: AttentionMode,
        attended: bool,
        participation_mode: ParticipationMode,
    ) -> None:
        del tomoko_participated
        self.rows.append((transcript, attended, attention_mode, participation_mode))


class InMemoryConversationLogWriter:
    def __init__(self) -> None:
        self.user_turns: list[tuple[Transcript, ParticipationMode]] = []
        self.tomoko_turns: list[tuple[str, str, str]] = []

    async def write_user_turn(
        self,
        transcript: Transcript,
        *,
        participation_mode: ParticipationMode,
    ) -> None:
        self.user_turns.append((transcript, participation_mode))

    async def write_tomoko_turn(
        self,
        *,
        text: str,
        emotion: str,
        device_id: str,
        status: str = "completed",
    ) -> None:
        del device_id
        self.tomoko_turns.append((text, emotion, status))


class FakeBackend(InferenceBackend):
    name = "fake"
    privacy_allowed = True

    async def chat_stream(
        self, system_prompt: str, messages: list[dict[str, str]]
    ) -> AsyncGenerator[str, None]:
        del system_prompt, messages
        yield "EMOTION:neutral\n"
        yield "今日はちょっと疲れてる？"


class FakeRouter:
    async def select(self, role: str, preference: str = "privacy") -> InferenceBackend:
        del role, preference
        return FakeBackend()


class FakeTTSBackend(TTSBackend):
    name = "fake_tts"

    async def synthesize(self, tts_input: TTSInput) -> AsyncGenerator[AudioChunkOut, None]:
        yield AudioChunkOut(
            data=f"audio:{tts_input.text}".encode(),
            sequence=0,
            is_last=True,
        )


async def run_one_finished_speech(session: TomoroSession) -> None:
    for _ in range(14):
        await session.process_audio_chunk(np.ones(512, dtype=np.float32).tobytes())
    await asyncio.sleep(0)


@pytest.mark.unit
def test_barge_in_detector_treats_tomoko_text_as_echo() -> None:
    detector = BargeInDetector()

    decision = detector.classify(
        BargeInContext(
            transcript="今日はちょっと疲れてる",
            recent_tomoko_text="今日はちょっと疲れてる？",
            speaking_elapsed_ms=1200,
        )
    )

    assert decision.kind == "echo"
    assert decision.action == "continue_speaking"


@pytest.mark.unit
def test_barge_in_detector_does_not_treat_opposite_reply_as_echo() -> None:
    detector = BargeInDetector()

    decision = detector.classify(
        BargeInContext(
            transcript="うん、疲れてる",
            recent_tomoko_text="今日はちょっと疲れてる？",
            speaking_elapsed_ms=1200,
        )
    )

    assert decision.kind == "backchannel"
    assert decision.action == "continue_speaking"


@pytest.mark.unit
def test_barge_in_detector_classifies_hard_interrupt() -> None:
    detector = BargeInDetector()

    decision = detector.classify(
        BargeInContext(
            transcript="違う違う、ちょっと待って",
            recent_tomoko_text="今日はちょっと疲れてる？",
            speaking_elapsed_ms=1200,
        )
    )

    assert decision.kind == "hard_interrupt"
    assert decision.action == "restart_turn"


@pytest.mark.unit
def test_session_default_playback_echo_grace_is_1200ms() -> None:
    session = TomoroSession(
        vad_processor=VADProcessor(
            vad=SequenceVAD([0.1]),
            silence_ms=400,
        ),
        send_event=lambda event: None,
    )

    assert session._playback_echo_grace_ms == 1200


@pytest.mark.unit
async def test_session_filters_tomoko_echo_during_playback_window() -> None:
    ambient_logs = InMemoryAmbientLogWriter()
    conversation_logs = InMemoryConversationLogWriter()
    events: list[dict[str, str]] = []
    session = TomoroSession(
        vad_processor=VADProcessor(
            vad=SequenceVAD(([0.9] + [0.1] * 13) * 2),
            silence_ms=400,
        ),
        send_event=events.append,
        transcriber=QueueTranscriber(["トモコ、聞こえる？", "今日はちょっと疲れてる"]),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=ambient_logs,
        conversation_log_writer=conversation_logs,
        router=FakeRouter(),  # type: ignore[arg-type]
        thinking_mode=ThinkFastMode(),
        tts_backend=FakeTTSBackend(),
        barge_in_detector=BargeInDetector(),
    )

    await run_one_finished_speech(session)
    await session._wait_for_reply_task()
    await run_one_finished_speech(session)

    assert [row[3] for row in ambient_logs.rows] == ["called", "observer"]
    assert [turn[0].text for turn in conversation_logs.user_turns] == [
        "トモコ、聞こえる？"
    ]
    assert {"type": "barge_in", "kind": "echo", "action": "continue_speaking"} in events


@pytest.mark.unit
async def test_session_keeps_hard_interrupt_as_participation() -> None:
    ambient_logs = InMemoryAmbientLogWriter()
    conversation_logs = InMemoryConversationLogWriter()
    events: list[dict[str, str]] = []
    session = TomoroSession(
        vad_processor=VADProcessor(
            vad=SequenceVAD(([0.9] + [0.1] * 13) * 2),
            silence_ms=400,
        ),
        send_event=events.append,
        transcriber=QueueTranscriber(["トモコ、聞こえる？", "違う違う、待って"]),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=ambient_logs,
        conversation_log_writer=conversation_logs,
        router=FakeRouter(),  # type: ignore[arg-type]
        thinking_mode=ThinkFastMode(),
        tts_backend=FakeTTSBackend(),
        barge_in_detector=BargeInDetector(),
    )

    await run_one_finished_speech(session)
    await session._wait_for_reply_task()
    await run_one_finished_speech(session)

    assert [row[3] for row in ambient_logs.rows] == ["called", "invited"]
    assert [turn[0].text for turn in conversation_logs.user_turns] == [
        "トモコ、聞こえる？",
        "違う違う、待って",
    ]
    assert {"type": "barge_in", "kind": "hard_interrupt", "action": "restart_turn"} in events
    stop_events = [event for event in events if event["type"] == "audio_control"]
    assert len(stop_events) == 1
    assert stop_events[0]["action"] == "stop"
    assert stop_events[0]["turn_id"]


# --- Recent speech text echo guard tests ---


@pytest.mark.unit
def test_is_recent_echo_detects_timer_notification_within_window() -> None:
    detector = BargeInDetector()

    result = detector.is_recent_echo(
        "タイマーが鳴りました。1分タイマー。",
        "タイマーが鳴りました。1分タイマー。",
        time_since_sec=4.0,
        window_sec=8.0,
    )

    assert result is True


@pytest.mark.unit
def test_is_recent_echo_ignores_after_window_expires() -> None:
    detector = BargeInDetector()

    result = detector.is_recent_echo(
        "タイマーが鳴りました。1分タイマー。",
        "タイマーが鳴りました。1分タイマー。",
        time_since_sec=9.0,
        window_sec=8.0,
    )

    assert result is False


@pytest.mark.unit
def test_is_recent_echo_passes_hard_interrupt_through() -> None:
    detector = BargeInDetector()

    result = detector.is_recent_echo(
        "ストップ",
        "タイマーが鳴りました。1分タイマー。",
        time_since_sec=1.0,
        window_sec=8.0,
    )

    assert result is False


@pytest.mark.unit
async def test_session_filters_timer_echo_after_playback_windows_expire() -> None:
    """Echo arrives 4s after speech ends — timing guards gone, text guard catches it."""
    events: list[dict[str, object]] = []
    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.1]), silence_ms=400),
        send_event=events.append,
        barge_in_detector=BargeInDetector(),
    )

    # Simulate: Tomoko spoke a timer notification 4 seconds ago, windows now closed.
    notice_text = "タイマーが鳴りました。1分タイマー。"
    session.audio_turns._recent_tomoko_text = notice_text
    session.audio_turns._last_tomoko_speech_at = time.monotonic() - 4.0
    session.audio_turns._tomoko_speaking_until = 0.0
    session.audio_turns._playback_echo_until = 0.0

    echo_transcript = Transcript(
        text=notice_text,
        device_id="desk",
        speaker=None,
        audio_level_db=-20.0,
        recorded_at=datetime.now(UTC),
        is_final=True,
    )
    await session.process_transcript(echo_transcript)

    barge_in_events = [e for e in events if e.get("type") == "barge_in"]
    assert len(barge_in_events) == 1
    assert barge_in_events[0]["kind"] == "echo"
    assert barge_in_events[0]["action"] == "continue_speaking"


@pytest.mark.unit
async def test_session_hard_interrupt_passes_through_recent_echo_guard() -> None:
    """Hard interrupt is never suppressed even within the text echo window."""
    events: list[dict[str, object]] = []
    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.1]), silence_ms=400),
        send_event=events.append,
        barge_in_detector=BargeInDetector(),
        participation_judge=WakeWordJudge(),
    )

    session.audio_turns._recent_tomoko_text = "タイマーが鳴りました。1分タイマー。"
    session.audio_turns._last_tomoko_speech_at = time.monotonic() - 2.0
    session.audio_turns._tomoko_speaking_until = 0.0
    session.audio_turns._playback_echo_until = 0.0

    hard_interrupt = Transcript(
        text="ストップ",
        device_id="desk",
        speaker=None,
        audio_level_db=-20.0,
        recorded_at=datetime.now(UTC),
        is_final=True,
    )
    await session.process_transcript(hard_interrupt)

    barge_in_events = [e for e in events if e.get("type") == "barge_in"]
    assert not barge_in_events


@pytest.mark.unit
async def test_session_suppresses_followup_during_playback_ended_grace() -> None:
    ambient_logs = InMemoryAmbientLogWriter()
    conversation_logs = InMemoryConversationLogWriter()
    events: list[dict[str, str]] = []
    session = TomoroSession(
        vad_processor=VADProcessor(
            vad=SequenceVAD([0.9] + [0.1] * 13),
            silence_ms=400,
        ),
        send_event=events.append,
        transcriber=QueueTranscriber(["それで、どうする？"]),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=ambient_logs,
        conversation_log_writer=conversation_logs,
        router=FakeRouter(),  # type: ignore[arg-type]
        thinking_mode=ThinkFastMode(),
        tts_backend=FakeTTSBackend(),
        barge_in_detector=BargeInDetector(),
    )
    await session._transition_attention("cooldown")
    await session.handle_playback_telemetry(
        PlaybackTelemetry(
            type="playback_ended",
            turn_id="turn-1",
            chunk_id=1,
        )
    )

    await run_one_finished_speech(session)

    assert [row[3] for row in ambient_logs.rows] == ["observer"]
    assert conversation_logs.user_turns == []
    assert {
        "type": "barge_in",
        "kind": "echo",
        "action": "continue_speaking",
    } in events


@pytest.mark.unit
async def test_session_suppresses_followup_while_playback_chunk_is_active() -> None:
    ambient_logs = InMemoryAmbientLogWriter()
    conversation_logs = InMemoryConversationLogWriter()
    events: list[dict[str, str]] = []
    session = TomoroSession(
        vad_processor=VADProcessor(
            vad=SequenceVAD([0.9] + [0.1] * 13),
            silence_ms=400,
        ),
        send_event=events.append,
        transcriber=QueueTranscriber(["それで、どうする？"]),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=ambient_logs,
        conversation_log_writer=conversation_logs,
        router=FakeRouter(),  # type: ignore[arg-type]
        thinking_mode=ThinkFastMode(),
        tts_backend=FakeTTSBackend(),
        barge_in_detector=BargeInDetector(),
    )
    await session._transition_attention("engaged")
    session.audio_turns.begin_turn()
    await session._send_reserved_audio_start()
    active_turn_id = session.audio_turns.active_turn_id
    await session.handle_playback_telemetry(
        PlaybackTelemetry(
            type="playback_started",
            turn_id=active_turn_id,
            chunk_id=5,
        )
    )

    await run_one_finished_speech(session)

    assert [row[3] for row in ambient_logs.rows] == ["observer"]
    assert conversation_logs.user_turns == []
    assert {
        "type": "barge_in",
        "kind": "echo",
        "action": "continue_speaking",
    } in events


@pytest.mark.unit
async def test_session_routes_wait_interrupt_through_turn_taking_during_playback() -> None:
    ambient_logs = InMemoryAmbientLogWriter()
    events: list[dict[str, str]] = []
    session = TomoroSession(
        vad_processor=VADProcessor(
            vad=SequenceVAD([0.9] + [0.1] * 13),
            silence_ms=400,
        ),
        send_event=events.append,
        send_audio=lambda chunk: None,
        transcriber=QueueTranscriber(["ちょっと待って"]),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=ambient_logs,
        router=FakeRouter(),  # type: ignore[arg-type]
        thinking_mode=ThinkFastMode(),
        tts_backend=FakeTTSBackend(),
        barge_in_detector=BargeInDetector(),
    )
    await session._transition_attention("engaged")
    session.audio_turns.begin_turn()
    await session._send_reserved_audio_start()
    active_turn_id = session.audio_turns.active_turn_id
    await session.handle_playback_telemetry(
        PlaybackTelemetry(
            type="playback_started",
            turn_id=active_turn_id,
            chunk_id=5,
        )
    )

    await run_one_finished_speech(session)

    assert {
        "type": "turn_taking_decision",
        "decision": "stop_speaking",
        "reason": "wait_keyword",
        "source": "rule",
    } in events
    assert any(
        event["type"] == "audio_control" and event["action"] == "stop"
        for event in events
    )
    assert [row[3] for row in ambient_logs.rows] == ["observer"]


@pytest.mark.unit
async def test_session_routes_wait_inflection_through_turn_taking_during_playback() -> None:
    events: list[dict[str, str]] = []
    session = TomoroSession(
        vad_processor=VADProcessor(
            vad=SequenceVAD([0.9] + [0.1] * 13),
            silence_ms=400,
        ),
        send_event=events.append,
        send_audio=lambda chunk: None,
        transcriber=QueueTranscriber(["この映像ちょっとちょっと待とうか"]),
        participation_judge=WakeWordJudge(),
        router=FakeRouter(),  # type: ignore[arg-type]
        thinking_mode=ThinkFastMode(),
        tts_backend=FakeTTSBackend(),
        barge_in_detector=BargeInDetector(),
    )
    await session._transition_attention("engaged")
    session.audio_turns.begin_turn()
    await session._send_reserved_audio_start()
    await session.handle_playback_telemetry(
        PlaybackTelemetry(
            type="playback_started",
            turn_id=session.audio_turns.active_turn_id,
            chunk_id=5,
        )
    )

    await run_one_finished_speech(session)

    assert {
        "type": "turn_taking_decision",
        "decision": "stop_speaking",
        "reason": "wait_keyword",
        "source": "rule",
    } in events
    assert not any(
        event.get("type") == "barge_in" and event.get("reason") == "playback_active_chunk"
        for event in events
    )


@pytest.mark.unit
async def test_session_keeps_hard_interrupt_while_playback_chunk_is_active() -> None:
    ambient_logs = InMemoryAmbientLogWriter()
    conversation_logs = InMemoryConversationLogWriter()
    events: list[dict[str, str]] = []
    session = TomoroSession(
        vad_processor=VADProcessor(
            vad=SequenceVAD([0.9] + [0.1] * 13),
            silence_ms=400,
        ),
        send_event=events.append,
        transcriber=QueueTranscriber(["違う違う、待って"]),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=ambient_logs,
        conversation_log_writer=conversation_logs,
        router=FakeRouter(),  # type: ignore[arg-type]
        thinking_mode=ThinkFastMode(),
        tts_backend=FakeTTSBackend(),
        barge_in_detector=BargeInDetector(),
    )
    await session._transition_attention("engaged")
    await session.handle_playback_telemetry(
        PlaybackTelemetry(
            type="playback_started",
            turn_id="turn-1",
            chunk_id=5,
        )
    )

    await run_one_finished_speech(session)

    assert [row[3] for row in ambient_logs.rows] == ["invited"]
    assert [turn[0].text for turn in conversation_logs.user_turns] == [
        "違う違う、待って"
    ]
    assert {"type": "barge_in", "kind": "hard_interrupt", "action": "restart_turn"} in events
