from __future__ import annotations

import asyncio

import pytest

from server.gateway.audio_turn import AudioTurnController
from server.shared.models import AudioChunkOut, OutputLane, PlaybackTelemetry


@pytest.mark.unit
def test_output_lane_names_are_fixed() -> None:
    lanes: tuple[OutputLane, ...] = (
        "reply_turn",
        "initiative_turn",
        "gesture_audio",
        "stop_ack",
        "interrupting_turn",
    )

    assert lanes == (
        "reply_turn",
        "initiative_turn",
        "gesture_audio",
        "stop_ack",
        "interrupting_turn",
    )


@pytest.mark.unit
async def test_audio_turn_controller_reserves_start_and_end_for_same_turn() -> None:
    controller = AudioTurnController()
    controller.begin_turn(lane="reply_turn")

    start = await controller.reserve_start_event()
    duplicate_start = await controller.reserve_start_event()
    end = await controller.reserve_end_event()
    duplicate_end = await controller.reserve_end_event()

    assert start is not None
    assert start["type"] == "audio_start"
    assert duplicate_start is None
    assert end == {"type": "audio_end", "turn_id": start["turn_id"]}
    assert duplicate_end is None


@pytest.mark.unit
async def test_audio_turn_controller_rejects_gesture_audio_lane() -> None:
    controller = AudioTurnController()

    with pytest.raises(ValueError, match="gesture_audio"):
        controller.begin_turn(lane="gesture_audio")


@pytest.mark.unit
async def test_audio_turn_controller_sequences_chunks_under_concurrency() -> None:
    controller = AudioTurnController()

    chunks = await asyncio.gather(
        controller.reserve_audio_chunk(
            text="こんにちは。",
            chunk=AudioChunkOut(data=b"one", sequence=0, is_last=False),
        ),
        controller.reserve_audio_chunk(
            text="続きです。",
            chunk=AudioChunkOut(data=b"two", sequence=0, is_last=True),
        ),
    )

    assert sorted(chunk.sequence for chunk in chunks) == [0, 1]
    assert controller.is_tomoko_speaking() is True
    assert "こんにちは。" in controller.recent_tomoko_text
    assert "続きです。" in controller.recent_tomoko_text


@pytest.mark.unit
async def test_audio_turn_controller_tracks_active_playback_chunks_and_grace() -> None:
    controller = AudioTurnController(playback_echo_grace_ms=1200)

    await controller.handle_playback_telemetry(
        PlaybackTelemetry(type="playback_started", turn_id="turn-1", chunk_id=1)
    )
    assert controller.is_client_playback_active() is True

    await controller.handle_playback_telemetry(
        PlaybackTelemetry(type="playback_ended", turn_id="turn-1", chunk_id=1)
    )
    assert controller.is_client_playback_active() is False
    assert controller.is_playback_echo_grace_active() is True


@pytest.mark.unit
async def test_audio_turn_controller_ignores_playback_telemetry_without_turn_id() -> None:
    controller = AudioTurnController(playback_echo_grace_ms=1200)

    await controller.handle_playback_telemetry(
        PlaybackTelemetry(type="playback_started", turn_id=None, chunk_id=1)
    )
    await controller.handle_playback_telemetry(
        PlaybackTelemetry(type="playback_ended", turn_id=None, chunk_id=1)
    )

    assert controller.is_client_playback_active() is False
    assert controller.is_playback_echo_grace_active() is False
    assert controller.playback_state == "idle"


@pytest.mark.unit
async def test_audio_turn_controller_stop_event_is_reserved_once() -> None:
    controller = AudioTurnController()
    controller.begin_turn()
    start = await controller.reserve_start_event()

    stop, duplicate_stop = await asyncio.gather(
        controller.reserve_stop_event(),
        controller.reserve_stop_event(),
    )

    events = [event for event in (stop, duplicate_stop) if event is not None]
    assert start is not None
    assert events == [
        {
            "type": "audio_control",
            "action": "stop",
            "turn_id": start["turn_id"],
        }
    ]
