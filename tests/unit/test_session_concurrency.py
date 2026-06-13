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
from server.shared.models import AudioChunkOut, PlaybackTelemetry, Transcript, TTSInput


class QuietVAD:
    def process_chunk(self, chunk: np.ndarray) -> float:
        del chunk
        return 0.0


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


class FakeRouter:
    def __init__(self, backend: InferenceBackend) -> None:
        self.backend = backend

    async def select(self, role: str, preference: str = "privacy") -> InferenceBackend:
        del role, preference
        return self.backend


class BlockingTTS(TTSBackend):
    name = "blocking_tts"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def synthesize(self, tts_input: TTSInput) -> AsyncGenerator[AudioChunkOut, None]:
        del tts_input
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        yield AudioChunkOut(data=b"audio", sequence=0, is_last=True)


def _session(send_event, *, send_audio=None, **kwargs) -> TomoroSession:
    return TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=send_event,
        send_audio=send_audio,
        **kwargs,
    )


def _transcript(text: str) -> Transcript:
    return Transcript(
        text=text,
        device_id="test",
        speaker=None,
        audio_level_db=-18.0,
        recorded_at=datetime.now(UTC),
        is_final=True,
    )


@pytest.mark.unit
async def test_precomputed_reply_sends_audio_events_around_chunk() -> None:
    timeline: list[str] = []
    audio = b"RIFF\x24\x00\x00\x00WAVEfmt cached"

    async def send_event(event: dict[str, str]) -> None:
        if event["type"] in {"audio_start", "audio_end"}:
            timeline.append(f"{event['type']}:{event['turn_id']}")
        else:
            timeline.append(event["type"])

    async def send_audio(chunk: bytes) -> None:
        assert chunk == audio
        timeline.append("audio")

    session = _session(send_event, send_audio=send_audio)

    await session.start_precomputed_reply(
        text="今ならすぐ言えるよ。",
        device_id="desk",
        reason="phase108_unit",
        audio_data=audio,
    )

    reply_start_index = timeline.index("reply_text")
    output_timeline = timeline[reply_start_index:]
    assert output_timeline[0] == "reply_text"
    assert output_timeline[1].startswith("audio_start:")
    assert output_timeline[2] == "audio"
    assert output_timeline[3].startswith("audio_end:")
    assert output_timeline[4] == "reply_done"
    assert output_timeline[1].removeprefix("audio_start:") == output_timeline[
        3
    ].removeprefix(
        "audio_end:"
    )


@pytest.mark.unit
async def test_precomputed_reply_treats_audio_disconnect_as_closed_output() -> None:
    events: list[str] = []

    async def send_event(event: dict[str, str]) -> None:
        events.append(event["type"])

    async def send_audio(chunk: bytes) -> None:
        del chunk
        raise RuntimeError('Cannot call "send" once a close message has been sent.')

    session = _session(send_event, send_audio=send_audio)

    await session.start_precomputed_reply(
        text="結果をまとめるね。",
        device_id="desk",
        reason="research_answer",
        audio_data=b"RIFF\x24\x00\x00\x00WAVEfmt cached",
        output_lane="reply_turn",
    )

    assert events == ["attention", "reply_text", "audio_start"]


@pytest.mark.unit
async def test_concurrent_hard_interrupt_sends_one_stop_event() -> None:
    events: list[dict[str, str]] = []
    tts = BlockingTTS()
    session = _session(
        events.append,
        send_audio=lambda chunk: None,
        participation_judge=WakeWordJudge(),
        router=FakeRouter(FakeBackend(["長めに話すね。"])),  # type: ignore[arg-type]
        thinking_mode=ThinkFastMode(),
        tts_backend=tts,
        barge_in_detector=BargeInDetector(),
    )

    await session.process_transcript(_transcript("トモコ、聞こえる？"))
    await asyncio.wait_for(tts.started.wait(), timeout=1)

    await asyncio.gather(
        session.process_transcript(_transcript("ストップ")),
        session.process_transcript(_transcript("ストップ")),
    )

    stop_events = [event for event in events if event["type"] == "audio_control"]
    assert tts.cancelled is True
    assert stop_events == [
        {
            "type": "audio_control",
            "action": "stop",
            "turn_id": stop_events[0]["turn_id"],
        }
    ]


@pytest.mark.unit
async def test_playback_telemetry_updates_runtime_snapshot() -> None:
    session = _session(lambda event: None)

    await session.handle_playback_telemetry(
        PlaybackTelemetry(type="playback_started", turn_id="turn-1", chunk_id=3)
    )
    assert session.get_now_state().playback_state == "client_playing"

    await session.handle_playback_telemetry(
        PlaybackTelemetry(type="playback_ended", turn_id="turn-1", chunk_id=3)
    )
    assert session.get_now_state().playback_state == "echo_grace"
