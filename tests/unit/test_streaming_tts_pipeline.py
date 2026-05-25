from __future__ import annotations

import asyncio
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
from server.shared.models import AudioChunkOut, SpeechSegment, Transcript, TTSInput


class QuietVAD:
    def process_chunk(self, chunk: np.ndarray) -> float:
        del chunk
        return 0.0


class SequenceTranscriber:
    def __init__(self, texts: list[str]) -> None:
        self.texts = texts

    async def transcribe(self, segment: SpeechSegment) -> Transcript:
        return Transcript(
            text=self.texts.pop(0),
            device_id=segment.device_id,
            speaker=None,
            audio_level_db=-18.0,
            recorded_at=datetime.now(UTC),
            is_final=True,
        )


class FakeBackend(InferenceBackend):
    name = "fake"
    privacy_allowed = True

    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks

    async def chat_stream(
        self, system_prompt: str, messages: list[dict[str, str]]
    ) -> AsyncGenerator[str, None]:
        del system_prompt, messages
        for chunk in self.chunks:
            await asyncio.sleep(0)
            yield chunk


class BlockingBackend(InferenceBackend):
    name = "blocking"
    privacy_allowed = True

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False

    async def chat_stream(
        self, system_prompt: str, messages: list[dict[str, str]]
    ) -> AsyncGenerator[str, None]:
        del system_prompt, messages
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        yield "うん。"


class FakeRouter:
    def __init__(self, backend: InferenceBackend) -> None:
        self.backend = backend

    async def select(self, role: str, preference: str = "privacy") -> InferenceBackend:
        del role, preference
        return self.backend


class InMemoryConversationLogWriter:
    def __init__(self) -> None:
        self.user_turns: list[tuple[str, str]] = []
        self.tomoko_turns: list[tuple[str, str, str]] = []

    async def write_user_turn(
        self,
        transcript: Transcript,
        *,
        participation_mode: str,
    ) -> None:
        self.user_turns.append((transcript.text, participation_mode))

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

    async def read_recent_turns(self, *, limit: int) -> list[object]:
        del limit
        return []


class BlockingStreamingTTS(TTSBackend):
    name = "blocking_streaming_tts"

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.cancelled = False

    async def synthesize(self, tts_input: TTSInput) -> AsyncGenerator[AudioChunkOut, None]:
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        yield AudioChunkOut(
            data=f"audio:{tts_input.text}".encode(),
            sequence=0,
            is_last=False,
        )


def _segment() -> SpeechSegment:
    return SpeechSegment(
        audio=np.ones(1600, dtype=np.float32),
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        device_id="test",
        vad_confidence=0.9,
    )


@pytest.mark.unit
async def test_reply_task_does_not_block_audio_processing_while_tts_waits() -> None:
    events: list[dict[str, str]] = []
    audio_chunks: list[bytes] = []
    tts = BlockingStreamingTTS()
    session = TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=events.append,
        send_audio=audio_chunks.append,
        transcriber=SequenceTranscriber(["トモコ、聞こえる？"]),
        participation_judge=WakeWordJudge(),
        router=FakeRouter(FakeBackend(["うん。"])),  # type: ignore[arg-type]
        thinking_mode=ThinkFastMode(),
        tts_backend=tts,
    )

    await session._handle_finished_speech(_segment())
    await asyncio.wait_for(tts.started.wait(), timeout=1)

    assert session._is_reply_generation_active() is True
    await asyncio.wait_for(
        session.process_audio_chunk(np.zeros(512, dtype=np.float32).tobytes()),
        timeout=1,
    )

    tts.release.set()
    await session._wait_for_reply_task()

    assert audio_chunks == ["audio:うん。".encode()]
    assert {"type": "reply_done"} in events


@pytest.mark.unit
async def test_hard_barge_in_cancels_generating_tts_and_stops_playback() -> None:
    events: list[dict[str, str]] = []
    tts = BlockingStreamingTTS()
    conversation_logs = InMemoryConversationLogWriter()
    session = TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=events.append,
        send_audio=lambda chunk: None,
        transcriber=SequenceTranscriber(["トモコ、聞こえる？", "ストップ"]),
        participation_judge=WakeWordJudge(),
        conversation_log_writer=conversation_logs,  # type: ignore[arg-type]
        router=FakeRouter(FakeBackend(["長めに話すね。"])),  # type: ignore[arg-type]
        thinking_mode=ThinkFastMode(),
        tts_backend=tts,
        barge_in_detector=BargeInDetector(),
    )

    await session._handle_finished_speech(_segment())
    await asyncio.wait_for(tts.started.wait(), timeout=1)
    await session._handle_finished_speech(_segment())

    assert tts.cancelled is True
    assert conversation_logs.tomoko_turns == [
        ("長めに話すね。", "neutral", "interrupted")
    ]
    assert any(
        event["type"] == "audio_control" and event["action"] == "stop"
        for event in events
    )


@pytest.mark.unit
async def test_new_listening_cancels_unstarted_reply_as_stale() -> None:
    events: list[dict[str, str]] = []
    backend = BlockingBackend()
    session = TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=events.append,
        send_audio=lambda chunk: None,
        transcriber=SequenceTranscriber(["トモコ、聞こえる？"]),
        participation_judge=WakeWordJudge(),
        router=FakeRouter(backend),  # type: ignore[arg-type]
        thinking_mode=ThinkFastMode(),
        tts_backend=BlockingStreamingTTS(),
    )

    await session._handle_finished_speech(_segment())
    await asyncio.wait_for(backend.started.wait(), timeout=1)

    assert session._is_reply_generation_active() is True
    await session._transition("listening")
    await session._wait_for_reply_task()

    assert backend.cancelled is True
    assert session._is_reply_generation_active() is False
    assert not any(event["type"] == "reply_text" for event in events)
    assert {"type": "reply_done"} not in events
