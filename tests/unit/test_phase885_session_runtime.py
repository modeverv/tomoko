from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from server.edge.pipeline.vad import VADProcessor
from server.gateway.turn_taking.barge_in import BargeInDetector
from server.session import TomoroSession
from server.shared.models import SessionEvent, Transcript


class QuietVAD:
    def process_chunk(self, chunk: np.ndarray) -> float:
        del chunk
        return 0.0


def _session() -> TomoroSession:
    return TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=lambda event: None,
        barge_in_detector=BargeInDetector(),
    )


def _transcript(text: str) -> Transcript:
    return Transcript(
        text=text,
        device_id="test-device",
        speaker=None,
        audio_level_db=-20.0,
        recorded_at=datetime.now(UTC),
        is_final=True,
    )


@pytest.mark.unit
async def test_post_event_updates_playback_runtime_state() -> None:
    session = _session()

    started = await session.post_event(
        SessionEvent(
            type="playback_started",
            payload={"turn_id": "turn-1", "chunk_id": 1},
        )
    )

    assert started.state.playback_state == "client_playing"
    assert session.get_now_state().playback_state == "client_playing"
    assert [command.type for command in started.commands] == [
        "record_playback_telemetry"
    ]

    ended = await session.post_event(
        SessionEvent(
            type="playback_ended",
            payload={"turn_id": "turn-1", "chunk_id": 1},
        )
    )

    assert ended.state.playback_state == "echo_grace"
    assert session.get_now_state().playback_state == "echo_grace"


@pytest.mark.unit
async def test_post_event_ignores_playback_runtime_state_without_turn_id() -> None:
    session = _session()

    started = await session.post_event(
        SessionEvent(
            type="playback_started",
            payload={"turn_id": None, "chunk_id": 1},
        )
    )
    ended = await session.post_event(
        SessionEvent(
            type="playback_ended",
            payload={"turn_id": None, "chunk_id": 1},
        )
    )

    assert started.state.playback_state == "idle"
    assert ended.state.playback_state == "idle"
    assert session.get_now_state().playback_state == "idle"


@pytest.mark.unit
async def test_reduce_marks_active_playback_transcript_as_echo_observer() -> None:
    session = _session()
    await session.post_event(
        SessionEvent(
            type="playback_started",
            payload={"turn_id": "turn-1", "chunk_id": 1},
        )
    )

    result = session._reduce(
        SessionEvent(
            type="transcript_finalized",
            payload={"transcript": _transcript("それで、どうする？")},
        )
    )

    assert result.emissions[0].type == "barge_in_resolved"
    assert result.emissions[0].payload["kind"] == "echo"
    assert result.emissions[0].payload["reason"] == "playback_active_chunk"
    assert [command.type for command in result.commands] == ["write_ambient_observer"]


@pytest.mark.unit
async def test_reduce_returns_interrupt_commands_for_hard_interrupt() -> None:
    session = _session()
    await session.post_event(
        SessionEvent(
            type="playback_started",
            payload={"turn_id": "turn-1", "chunk_id": 1},
        )
    )

    result = session._reduce(
        SessionEvent(
            type="transcript_finalized",
            payload={"transcript": _transcript("違う違う、待って")},
        )
    )

    assert result.emissions[0].payload["kind"] == "hard_interrupt"
    assert result.emissions[0].payload["action"] == "restart_turn"
    assert [command.type for command in result.commands] == [
        "cancel_reply_generation",
        "send_audio_control_stop",
        "save_tomoko_turn",
        "start_reply_generation",
    ]
    assert result.commands[0].payload["status"] == "interrupted"
    assert result.commands[2].payload["status"] == "interrupted"
