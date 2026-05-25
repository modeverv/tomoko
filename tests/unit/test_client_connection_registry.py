from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from server.gateway.connections import ClientConnectionRegistry


@pytest.mark.unit
def test_connection_registry_exposes_output_state_without_websocket_objects() -> None:
    now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    registry = ClientConnectionRegistry(now_factory=lambda: now)

    snapshot = registry.register(
        connection_id="browser-1",
        device_id="desk",
        role="browser",
        can_receive_audio=True,
        can_receive_display=True,
    )

    assert snapshot.active_device_id == "desk"
    assert snapshot.audio_target_available is True
    assert snapshot.display_target_available is True
    assert snapshot.connected_device_count == 1
    assert snapshot.connected_connection_count == 1
    assert snapshot.last_presence_at == now


@pytest.mark.unit
def test_connection_registry_tracks_multiple_devices_and_disconnects() -> None:
    current = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)

    def now_factory() -> datetime:
        return current

    registry = ClientConnectionRegistry(now_factory=now_factory)
    registry.register(
        connection_id="monitor-1",
        device_id="desk",
        role="monitor",
        can_receive_audio=False,
        can_receive_display=True,
    )
    current += timedelta(seconds=5)
    registry.register(
        connection_id="edge-1",
        device_id="kitchen",
        role="edge",
        can_receive_audio=True,
        can_receive_display=True,
    )
    registry.set_playback_state(device_id="kitchen", playback_state="client_playing")

    snapshot = registry.snapshot()

    assert snapshot.active_device_id == "kitchen"
    assert snapshot.audio_target_available is True
    assert snapshot.connected_device_count == 2
    assert snapshot.connected_connection_count == 2
    assert snapshot.playback_state_by_device == {"kitchen": "client_playing"}

    after_disconnect = registry.unregister("edge-1")

    assert after_disconnect.active_device_id == "desk"
    assert after_disconnect.audio_target_available is False
    assert after_disconnect.display_target_available is True
    assert after_disconnect.playback_state_by_device == {}
