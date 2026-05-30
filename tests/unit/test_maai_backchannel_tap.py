from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pytest

from server.edge.pipeline.vad import VADProcessor
from server.gateway.turn_taking.barge_in import BargeInDetector
from server.session import TomoroSession
from server.shared.inference.tts.base import TTSBackend
from server.shared.models import (
    AudioChunkOut,
    BackchannelSuggestion,
    PlaybackTelemetry,
    SessionEvent,
    TTSInput,
)


class QuietVAD:
    def process_chunk(self, chunk: np.ndarray) -> float:
        del chunk
        return 0.0


class RecordingAudioTap:
    def __init__(self) -> None:
        self.user_chunks: list[np.ndarray] = []
        self.tomoko_chunks: list[bytes] = []

    def observe_user_audio(self, chunk: np.ndarray, *, observed_at: datetime) -> None:
        del observed_at
        self.user_chunks.append(chunk.copy())

    def observe_tomoko_audio(self, chunk: bytes, *, observed_at: datetime) -> None:
        del observed_at
        self.tomoko_chunks.append(chunk)


class FailingAudioTap:
    def observe_user_audio(self, chunk: np.ndarray, *, observed_at: datetime) -> None:
        del chunk, observed_at
        raise RuntimeError("tap user failure")

    def observe_tomoko_audio(self, chunk: bytes, *, observed_at: datetime) -> None:
        del chunk, observed_at
        raise RuntimeError("tap tomoko failure")


class RecordingTTSBackend(TTSBackend):
    name = "recording_tts"

    def __init__(self) -> None:
        self.inputs: list[TTSInput] = []

    async def synthesize(
        self,
        tts_input: TTSInput,
    ) -> AsyncGenerator[AudioChunkOut, None]:
        self.inputs.append(tts_input)
        yield AudioChunkOut(
            data=f"audio:{tts_input.text}".encode(),
            sequence=0,
            is_last=True,
        )


def _session(
    *,
    send_audio: Any | None = None,
    audio_interaction_tap: Any | None = None,
    send_event: Any | None = None,
    tts_backend: TTSBackend | None = None,
) -> TomoroSession:
    return TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=send_event or (lambda event: None),
        send_audio=send_audio,
        tts_backend=tts_backend,
        barge_in_detector=BargeInDetector(),
        audio_interaction_tap=audio_interaction_tap,
    )


@pytest.mark.unit
async def test_user_audio_is_copied_to_optional_interaction_tap() -> None:
    tap = RecordingAudioTap()
    session = _session(audio_interaction_tap=tap)
    chunk = np.linspace(-0.25, 0.25, 512, dtype=np.float32)

    segment = await session.process_audio_chunk(chunk.tobytes())

    assert segment is None
    assert len(tap.user_chunks) == 1
    np.testing.assert_array_equal(tap.user_chunks[0], chunk)


@pytest.mark.unit
async def test_audio_tap_failure_does_not_block_user_hot_path() -> None:
    session = _session(audio_interaction_tap=FailingAudioTap())
    chunk = np.ones(512, dtype=np.float32)

    segment = await session.process_audio_chunk(chunk.tobytes())

    assert segment is None
    assert session.get_now_state().vad_state == "idle"


@pytest.mark.unit
async def test_tomoko_audio_is_sent_and_copied_to_optional_interaction_tap() -> None:
    sent_audio: list[bytes] = []
    tap = RecordingAudioTap()
    session = _session(send_audio=sent_audio.append, audio_interaction_tap=tap)
    chunk = AudioChunkOut(data=b"RIFFfakeWAVE", sequence=0, is_last=True)

    await session._send_audio_chunk(chunk)

    assert sent_audio == [b"RIFFfakeWAVE"]
    assert tap.tomoko_chunks == [b"RIFFfakeWAVE"]


@pytest.mark.unit
async def test_tomoko_audio_tap_failure_does_not_block_audio_send() -> None:
    sent_audio: list[bytes] = []
    session = _session(send_audio=sent_audio.append, audio_interaction_tap=FailingAudioTap())
    chunk = AudioChunkOut(data=b"pcm", sequence=0, is_last=True)

    await session._send_audio_chunk(chunk)

    assert sent_audio == [b"pcm"]


@pytest.mark.unit
async def test_backchannel_suggestion_event_is_gated_before_audio_release() -> None:
    session = _session()
    suggestion = BackchannelSuggestion(
        kind="react",
        score=0.82,
        source="maai",
        observed_at=datetime.now(UTC),
    )

    result = await session.post_event(
        SessionEvent(
            type="backchannel_suggested",
            payload={"suggestion": suggestion},
        )
    )

    assert result.emissions[0].type == "backchannel_skipped"
    assert result.emissions[0].payload["kind"] == "react"
    assert result.emissions[0].payload["source"] == "maai"
    assert result.emissions[0].payload["score"] == pytest.approx(0.82)
    assert result.emissions[0].payload["reason"] == "user_not_speaking"
    assert result.commands == []


@pytest.mark.unit
async def test_maai_react_suggestion_releases_llm_less_backchannel_audio() -> None:
    events: list[dict[str, Any]] = []
    audio: list[bytes] = []
    tts = RecordingTTSBackend()
    session = _session(send_event=events.append, send_audio=audio.append, tts_backend=tts)
    await session._transition("listening")
    suggestion = BackchannelSuggestion(
        kind="react",
        score=0.69,
        source="maai",
        observed_at=datetime.now(UTC),
    )

    result = await session.apply_backchannel_suggestion(suggestion)

    assert result.emissions[0].type == "backchannel_released"
    assert tts.inputs[0].text in {"うん", "なるほど", "そっか"}
    assert tts.inputs[0].style == "gentle"
    assert audio == [f"audio:{tts.inputs[0].text}".encode()]
    assert {"type": "reply_done", "control": "backchannel"} in events


@pytest.mark.unit
async def test_maai_backchannel_is_once_per_user_speech_segment() -> None:
    audio: list[bytes] = []
    tts = RecordingTTSBackend()
    session = _session(send_audio=audio.append, tts_backend=tts)
    await session._transition("listening")
    suggestion = BackchannelSuggestion(
        kind="react",
        score=0.8,
        source="maai",
        observed_at=datetime.now(UTC),
    )

    first = await session.apply_backchannel_suggestion(suggestion)
    second = await session.apply_backchannel_suggestion(suggestion)

    assert first.emissions[0].type == "backchannel_released"
    assert second.emissions[0].type == "backchannel_skipped"
    assert second.emissions[0].payload["reason"] == "already_released_in_speech_segment"
    assert len(tts.inputs) == 1


@pytest.mark.unit
async def test_maai_backchannel_release_requires_user_speaking_and_idle_tomoko() -> None:
    tts = RecordingTTSBackend()
    session = _session(tts_backend=tts)
    suggestion = BackchannelSuggestion(
        kind="react",
        score=0.8,
        source="maai",
        observed_at=datetime.now(UTC),
    )

    idle_result = await session.apply_backchannel_suggestion(suggestion)
    await session._transition("listening")
    await session.handle_playback_telemetry(
        PlaybackTelemetry(type="playback_started", turn_id="turn-1", chunk_id=1)
    )
    playback_result = await session.apply_backchannel_suggestion(suggestion)

    assert idle_result.emissions[0].payload["reason"] == "user_not_speaking"
    assert playback_result.emissions[0].payload["reason"] == "tomoko_not_idle"
    assert tts.inputs == []


@pytest.mark.unit
async def test_maai_backchannel_release_applies_global_cooldown() -> None:
    audio: list[bytes] = []
    tts = RecordingTTSBackend()
    session = _session(send_audio=audio.append, tts_backend=tts)
    await session._transition("listening")
    first = BackchannelSuggestion(
        kind="react",
        score=0.8,
        source="maai",
        observed_at=datetime.now(UTC),
    )
    second = BackchannelSuggestion(
        kind="react",
        score=0.8,
        source="maai",
        observed_at=first.observed_at + timedelta(milliseconds=500),
    )

    await session.apply_backchannel_suggestion(first)
    await session._transition("idle")
    await session._transition("listening")
    session.audio_turns._tomoko_speaking_until = 0.0
    result = await session.apply_backchannel_suggestion(second)

    assert result.emissions[0].type == "backchannel_skipped"
    assert result.emissions[0].payload["reason"] == "cooldown_active"
    assert len(tts.inputs) == 1
