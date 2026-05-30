from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import numpy as np
import pytest

from server.edge.participation.wake_word import WakeWordJudge
from server.edge.pipeline.vad import VADProcessor
from server.gateway.thinking.fast import ThinkFastMode
from server.session import TomoroSession
from server.shared.inference.backends.base import InferenceBackend
from server.shared.models import (
    AttentionMode,
    AudioChunkOut,
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
    def __init__(self, texts: list[str], audio_levels: list[float] | None = None) -> None:
        self.texts = texts
        self.audio_levels = audio_levels or []

    async def transcribe(self, segment: SpeechSegment) -> Transcript:
        audio_level = self.audio_levels.pop(0) if self.audio_levels else -20.0
        return Transcript(
            text=self.texts.pop(0),
            device_id=segment.device_id,
            speaker=None,
            audio_level_db=audio_level,
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
        self.rows.append((transcript, attended, attention_mode, participation_mode))


class InMemoryConversationLogWriter:
    def __init__(self) -> None:
        self.user_turns: list[tuple[Transcript, ParticipationMode]] = []
        self.tomoko_turns: list[tuple[str, str]] = []

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
        yield "うん。"


class FakeRouter:
    async def select(self, role: str, preference: str = "privacy") -> InferenceBackend:
        del role, preference
        return FakeBackend()


class FakeTTSBackend:
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


@pytest.mark.unit
async def test_wake_word_moves_attention_to_engaged() -> None:
    events: list[dict[str, str]] = []
    ambient_logs = InMemoryAmbientLogWriter()
    conversation_logs = InMemoryConversationLogWriter()
    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.9] + [0.1] * 13), silence_ms=400),
        send_event=events.append,
        transcriber=QueueTranscriber(["トモコ、聞こえる？"]),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=ambient_logs,
        conversation_log_writer=conversation_logs,
        router=FakeRouter(),  # type: ignore[arg-type]
        thinking_mode=ThinkFastMode(),
        tts_backend=FakeTTSBackend(),  # type: ignore[arg-type]
    )

    await run_one_finished_speech(session)

    assert session.attention_mode == "engaged"
    assert {"type": "attention", "mode": "engaged"} in events
    assert ambient_logs.rows[0][1:] == (True, "ambient", "called")
    assert conversation_logs.user_turns[0][0].text == "トモコ、聞こえる？"
    assert conversation_logs.user_turns[0][1] == "called"


@pytest.mark.unit
async def test_engaged_allows_followup_without_wake_word() -> None:
    ambient_logs = InMemoryAmbientLogWriter()
    conversation_logs = InMemoryConversationLogWriter()
    session = TomoroSession(
        vad_processor=VADProcessor(
            vad=SequenceVAD(([0.9] + [0.1] * 13) * 2),
            silence_ms=400,
        ),
        send_event=lambda event: None,
        transcriber=QueueTranscriber(["トモコ、聞こえる？", "さっきの続きなんだけど"]),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=ambient_logs,
        conversation_log_writer=conversation_logs,
        router=FakeRouter(),  # type: ignore[arg-type]
        thinking_mode=ThinkFastMode(),
        tts_backend=FakeTTSBackend(),  # type: ignore[arg-type]
    )

    await run_one_finished_speech(session)
    await run_one_finished_speech(session)

    assert [row[3] for row in ambient_logs.rows] == ["called", "invited"]
    assert [turn[0].text for turn in conversation_logs.user_turns] == [
        "トモコ、聞こえる？",
        "さっきの続きなんだけど",
    ]


@pytest.mark.unit
async def test_engaged_short_unfinished_fragment_does_not_start_reply() -> None:
    events: list[dict[str, str]] = []
    ambient_logs = InMemoryAmbientLogWriter()
    conversation_logs = InMemoryConversationLogWriter()
    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.9] + [0.1] * 13), silence_ms=400),
        send_event=events.append,
        transcriber=QueueTranscriber(["相槌のタイミングで"]),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=ambient_logs,
        conversation_log_writer=conversation_logs,
        router=FakeRouter(),  # type: ignore[arg-type]
        thinking_mode=ThinkFastMode(),
        tts_backend=FakeTTSBackend(),  # type: ignore[arg-type]
    )
    await session._transition_attention("engaged")

    await run_one_finished_speech(session)

    assert [row[3] for row in ambient_logs.rows] == ["observer"]
    assert conversation_logs.user_turns == []
    assert {"type": "participation", "mode": "invited"} not in events
    assert not any(event["type"] == "reply_text" for event in events)


@pytest.mark.unit
async def test_engaged_filters_low_confidence_followup_without_extending_attention() -> None:
    ambient_logs = InMemoryAmbientLogWriter()
    conversation_logs = InMemoryConversationLogWriter()
    session = TomoroSession(
        vad_processor=VADProcessor(
            vad=SequenceVAD([0.9] + [0.1] * 13 + [0.0] * 2),
            silence_ms=400,
        ),
        send_event=lambda event: None,
        transcriber=QueueTranscriber(["お疲れ様です"], audio_levels=[-35.0]),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=ambient_logs,
        conversation_log_writer=conversation_logs,
        engaged_timeout_ms=64,
        cooldown_timeout_ms=64,
    )
    await session._transition_attention("engaged")

    await run_one_finished_speech(session)
    assert [row[3] for row in ambient_logs.rows] == ["observer"]
    assert conversation_logs.user_turns == []

    for _ in range(2):
        await session.process_audio_chunk(np.zeros(512, dtype=np.float32).tobytes())
    assert session.attention_mode == "cooldown"


@pytest.mark.unit
async def test_attention_decays_from_engaged_to_cooldown_to_ambient() -> None:
    events: list[dict[str, str]] = []
    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.0] * 64), silence_ms=400),
        send_event=events.append,
        participation_judge=WakeWordJudge(),
        engaged_timeout_ms=64,
        cooldown_timeout_ms=64,
    )
    await session._transition_attention("engaged")

    for _ in range(2):
        await session.process_audio_chunk(np.zeros(512, dtype=np.float32).tobytes())
    assert session.attention_mode == "cooldown"

    for _ in range(2):
        await session.process_audio_chunk(np.zeros(512, dtype=np.float32).tobytes())
    assert session.attention_mode == "ambient"
    assert {"type": "attention", "mode": "cooldown"} in events
    assert {"type": "attention", "mode": "ambient"} in events


@pytest.mark.unit
async def test_attention_idle_does_not_advance_while_playback_is_active() -> None:
    events: list[dict[str, str]] = []
    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.0] * 8), silence_ms=400),
        send_event=events.append,
        participation_judge=WakeWordJudge(),
        engaged_timeout_ms=64,
        cooldown_timeout_ms=64,
    )
    await session._transition_attention("engaged")
    await session.handle_playback_telemetry(
        PlaybackTelemetry(type="playback_started", turn_id="turn-1", chunk_id=1)
    )

    for _ in range(4):
        await session.process_audio_chunk(np.zeros(512, dtype=np.float32).tobytes())

    assert session.attention_mode == "engaged"
    assert {"type": "attention", "mode": "cooldown"} not in events


@pytest.mark.unit
async def test_ambient_speech_is_not_conversation_context() -> None:
    ambient_logs = InMemoryAmbientLogWriter()
    conversation_logs = InMemoryConversationLogWriter()
    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.9] + [0.1] * 13), silence_ms=400),
        send_event=lambda event: None,
        transcriber=QueueTranscriber(["今日いい天気だね"]),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=ambient_logs,
        conversation_log_writer=conversation_logs,
    )

    await run_one_finished_speech(session)

    assert ambient_logs.rows[0][1:] == (False, "ambient", "observer")
    assert conversation_logs.user_turns == []


@pytest.mark.unit
async def test_withdrawn_does_not_participate_until_called_back() -> None:
    ambient_logs = InMemoryAmbientLogWriter()
    session = TomoroSession(
        vad_processor=VADProcessor(
            vad=SequenceVAD(([0.9] + [0.1] * 13) * 2),
            silence_ms=400,
        ),
        send_event=lambda event: None,
        transcriber=QueueTranscriber(["今は静かにして", "この話どう思う？"]),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=ambient_logs,
        router=FakeRouter(),  # type: ignore[arg-type]
        thinking_mode=ThinkFastMode(),
        tts_backend=FakeTTSBackend(),  # type: ignore[arg-type]
    )

    await run_one_finished_speech(session)
    assert session.attention_mode == "withdrawn"

    await run_one_finished_speech(session)

    assert [row[3] for row in ambient_logs.rows] == ["withdraw", "withdraw"]
    assert [row[1] for row in ambient_logs.rows] == [False, False]
