from __future__ import annotations

import asyncio

import numpy as np
import pytest

from server.edge.pipeline.vad import VADProcessor
from server.session import TomoroSession
from server.shared.models import PlaybackTelemetry


class QuietVAD:
    def process_chunk(self, chunk: np.ndarray) -> float:
        del chunk
        return 0.0


def _session(send_event) -> TomoroSession:
    return TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=send_event,
    )


@pytest.mark.unit
async def test_audio_start_is_sent_once_under_concurrent_start() -> None:
    events: list[dict[str, str]] = []
    session = _session(lambda event: events.append(event))
    session._begin_audio_turn()

    async def slow_send_event(event: dict[str, str]) -> None:
        await asyncio.sleep(0)
        events.append(event)

    session.send_event = slow_send_event

    await asyncio.gather(
        session._ensure_audio_turn_started(),
        session._ensure_audio_turn_started(),
    )

    assert [event["type"] for event in events] == ["audio_start"]


@pytest.mark.unit
async def test_audio_stop_is_sent_once_under_concurrent_stop() -> None:
    events: list[dict[str, str]] = []
    session = _session(lambda event: events.append(event))
    session._begin_audio_turn()
    await session._ensure_audio_turn_started()

    async def slow_send_event(event: dict[str, str]) -> None:
        await asyncio.sleep(0)
        events.append(event)

    session.send_event = slow_send_event

    await asyncio.gather(
        session._stop_active_audio_turn(),
        session._stop_active_audio_turn(),
    )

    assert [event["type"] for event in events] == ["audio_start", "audio_control"]


@pytest.mark.unit
async def test_playback_telemetry_updates_active_chunks_with_async_contract() -> None:
    session = _session(lambda event: None)

    await session.handle_playback_telemetry(
        PlaybackTelemetry(type="playback_started", turn_id="turn-1", chunk_id=3)
    )
    assert session._is_client_playback_active() is True

    await session.handle_playback_telemetry(
        PlaybackTelemetry(type="playback_ended", turn_id="turn-1", chunk_id=3)
    )
    assert session._is_client_playback_active() is False
