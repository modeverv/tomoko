from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import numpy as np
import pytest

import server.session as session_module
import server.session_latency as latency_module
from server.edge.pipeline.vad import VADProcessor
from server.gateway.context import ContextSnapshotBuilder
from server.session import TomoroSession
from server.session_latency import LatencyProbeState
from server.shared.inference.backends.base import InferenceBackend
from server.shared.models import AudioChunkOut, ThinkingEvent, ThinkingInput, Transcript


class QuietVAD:
    def process_chunk(self, chunk: np.ndarray) -> float:
        del chunk
        return 0.0


class FakeBackend(InferenceBackend):
    name = "fake"
    privacy_allowed = True

    async def chat_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
    ) -> AsyncGenerator[str, None]:
        del system_prompt, messages
        yield ""


class FakeRouter:
    async def select(self, role: str, preference: str = "privacy") -> InferenceBackend:
        assert role == "conversation"
        assert preference == "privacy"
        return FakeBackend()


class TextOnlyThinkingMode:
    def __init__(self) -> None:
        self.inputs: list[ThinkingInput] = []

    async def think(
        self,
        backend: InferenceBackend,
        thinking_input: ThinkingInput,
    ) -> AsyncGenerator[ThinkingEvent, None]:
        del backend
        self.inputs.append(thinking_input)
        yield ThinkingEvent(type="text_delta", value="うん、聞こえるよ。")
        yield ThinkingEvent(type="done", value="")


class OneChunkTTS:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    async def synthesize(
        self,
        tts_input,
    ) -> AsyncGenerator[AudioChunkOut, None]:
        self.inputs.append(tts_input.text)
        yield AudioChunkOut(data=b"pcm", sequence=0, is_last=True)


def _session(**kwargs) -> TomoroSession:
    return TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=lambda event: None,
        **kwargs,
    )


def _transcript(text: str = "トモコ、聞こえる？") -> Transcript:
    return Transcript(
        text=text,
        device_id="browser",
        speaker=None,
        audio_level_db=-20.0,
        recorded_at=datetime.now(UTC),
        is_final=True,
    )


@pytest.mark.unit
def test_reset_latency_probe_resets_probe_fields_and_output_started_but_not_defer() -> None:
    session = _session()
    session._latency_probe.speech_end_at = 10.0
    session._latency_probe.reply_start_at = 11.0
    session._latency_probe.first_reply_text_at = 12.0
    session._latency_probe.tts_start_at = 13.0
    session._latency_probe.first_audio_chunk_at = 14.0
    session._latency_probe.reply_output_started = True
    session._latency_probe.reply_output_defer_until = 20.0

    session._reset_latency_probe()

    assert session._latency_probe.speech_end_at is None
    assert session._latency_probe.reply_start_at is None
    assert session._latency_probe.first_reply_text_at is None
    assert session._latency_probe.tts_start_at is None
    assert session._latency_probe.first_audio_chunk_at is None
    assert session._latency_probe.reply_output_started is False
    assert session._latency_probe.reply_output_defer_until == 20.0


@pytest.mark.unit
def test_elapsed_helpers_return_zero_without_marks_and_measure_from_mark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    assert session._elapsed_since_speech_end_ms() == 0.0
    assert session._elapsed_since_reply_start_ms() == 0.0
    assert session._elapsed_since_first_reply_text_ms() == 0.0
    assert session._elapsed_since_tts_start_ms() == 0.0
    assert session_module._elapsed_ms(session._latency_probe.first_audio_chunk_at) == 0.0

    now = 100.250
    monkeypatch.setattr(latency_module.time, "perf_counter", lambda: now)
    session._latency_probe.speech_end_at = 100.000
    session._latency_probe.reply_start_at = 100.050
    session._latency_probe.first_reply_text_at = 100.100
    session._latency_probe.tts_start_at = 100.150
    session._latency_probe.first_audio_chunk_at = 100.200

    assert session._elapsed_since_speech_end_ms() == pytest.approx(250.0)
    assert session._elapsed_since_reply_start_ms() == pytest.approx(200.0)
    assert session._elapsed_since_first_reply_text_ms() == pytest.approx(150.0)
    assert session._elapsed_since_tts_start_ms() == pytest.approx(100.0)
    assert session_module._elapsed_ms(
        session._latency_probe.first_audio_chunk_at
    ) == pytest.approx(50.0)


@pytest.mark.unit
def test_latency_probe_state_marks_and_resets_without_clearing_defer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_time = 30.0
    monkeypatch.setattr(latency_module.time, "perf_counter", lambda: current_time)
    probe = LatencyProbeState(reply_output_defer_until=40.0)

    probe.mark_speech_end()
    current_time = 30.010
    probe.mark_reply_start()
    current_time = 30.020
    assert probe.mark_first_reply_text_if_unmarked() is True
    assert probe.mark_first_reply_text_if_unmarked() is False
    current_time = 30.030
    assert probe.mark_tts_start_if_unmarked() is True
    current_time = 30.040
    assert probe.mark_first_audio_chunk_if_unmarked() is True
    probe.mark_reply_output_started()
    probe.reset()

    assert probe.speech_end_at is None
    assert probe.reply_start_at is None
    assert probe.first_reply_text_at is None
    assert probe.tts_start_at is None
    assert probe.first_audio_chunk_at is None
    assert probe.reply_output_started is False
    assert probe.reply_output_defer_until == 40.0


@pytest.mark.unit
async def test_reply_text_marks_first_reply_text_and_output_started() -> None:
    events: list[dict[str, object]] = []
    mode = TextOnlyThinkingMode()
    session = TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=events.append,
        router=FakeRouter(),  # type: ignore[arg-type]
        thinking_mode=mode,  # type: ignore[arg-type]
        context_snapshot_builder=ContextSnapshotBuilder(),
    )

    await session._reply_to(_transcript())

    assert mode.inputs
    assert session._latency_probe.reply_start_at is not None
    assert session._latency_probe.first_reply_text_at is not None
    assert session._latency_probe.reply_output_started is True
    assert {"type": "reply_text", "delta": "うん、聞こえるよ。"} in events
    assert {"type": "reply_done"} in events
    assert session._tts_queue is None
    assert session._tts_worker_task is None


@pytest.mark.unit
async def test_tts_chunk_marks_tts_start_first_audio_chunk_and_output_started() -> None:
    audio_chunks: list[bytes] = []
    tts = OneChunkTTS()
    session = _session(send_audio=audio_chunks.append, tts_backend=tts)  # type: ignore[arg-type]
    session.audio_turns.begin_turn()

    await session._flush_tts_text("うん。", style="neutral")

    assert tts.inputs == ["うん。"]
    assert audio_chunks == [b"pcm"]
    assert session._latency_probe.tts_start_at is not None
    assert session._latency_probe.first_audio_chunk_at is not None
    assert session._latency_probe.reply_output_started is True
    assert session._reply_task is None
    assert session._tts_queue is None
    assert session._tts_worker_task is None


@pytest.mark.unit
async def test_send_audio_chunk_marks_output_started_without_reply_or_tts_lifecycle() -> None:
    audio_chunks: list[bytes] = []
    session = _session(send_audio=audio_chunks.append)

    await session._send_audio_chunk(AudioChunkOut(data=b"pcm", sequence=0, is_last=True))

    assert audio_chunks == [b"pcm"]
    assert session._latency_probe.reply_output_started is True
    assert session._reply_task is None
    assert session._tts_queue is None
    assert session._tts_worker_task is None


@pytest.mark.unit
async def test_defer_reply_output_waits_until_deadline_and_clears_defer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_time = 10.0
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        nonlocal current_time
        sleeps.append(delay)
        current_time += delay

    monkeypatch.setattr(latency_module.time, "perf_counter", lambda: current_time)
    monkeypatch.setattr(session_module.asyncio, "sleep", fake_sleep)
    session = _session()

    session._defer_reply_output(max_ms=220)
    await session._maybe_wait_reply_output_defer()
    await session._maybe_wait_reply_output_defer()

    assert sleeps == [pytest.approx(0.220)]
    assert session._latency_probe.reply_output_defer_until is None


@pytest.mark.unit
async def test_defer_reply_output_keeps_later_deadline_and_caps_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_time = 20.0
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        nonlocal current_time
        sleeps.append(delay)
        current_time += delay

    monkeypatch.setattr(latency_module.time, "perf_counter", lambda: current_time)
    monkeypatch.setattr(session_module.asyncio, "sleep", fake_sleep)
    session = _session()

    session._defer_reply_output(max_ms=100)
    current_time = 20.020
    session._defer_reply_output(max_ms=400)
    current_time = 20.100
    await session._maybe_wait_reply_output_defer()

    assert sleeps == [0.25]
    assert session._latency_probe.reply_output_defer_until is None
