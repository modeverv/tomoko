from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from server.shared.models import (
    ClientConnection,
    ConnectedOutputState,
    ConnectionRole,
    PlaybackState,
)


def datetime_now_utc() -> datetime:
    return datetime.now(UTC)


class ClientConnectionRegistry:
    """Tracks connection facts without owning WebSocket objects."""

    def __init__(self, *, now_factory=datetime_now_utc) -> None:
        self._connections: dict[str, ClientConnection] = {}
        self._playback_state_by_device: dict[str, PlaybackState] = {}
        self._now_factory = now_factory

    def register(
        self,
        *,
        connection_id: str,
        device_id: str,
        role: ConnectionRole,
        can_receive_audio: bool,
        can_receive_display: bool,
    ) -> ConnectedOutputState:
        now = self._now_factory()
        self._connections[connection_id] = ClientConnection(
            connection_id=connection_id,
            device_id=device_id,
            role=role,
            can_receive_audio=can_receive_audio,
            can_receive_display=can_receive_display,
            connected_at=now,
            last_seen_at=now,
        )
        return self.snapshot()

    def unregister(self, connection_id: str) -> ConnectedOutputState:
        connection = self._connections.pop(connection_id, None)
        if connection is not None and not any(
            current.device_id == connection.device_id
            for current in self._connections.values()
        ):
            self._playback_state_by_device.pop(connection.device_id, None)
        return self.snapshot()

    def touch(self, connection_id: str) -> ConnectedOutputState:
        connection = self._connections.get(connection_id)
        if connection is not None:
            self._connections[connection_id] = replace(
                connection,
                last_seen_at=self._now_factory(),
            )
        return self.snapshot()

    def set_playback_state(
        self,
        *,
        device_id: str,
        playback_state: PlaybackState,
    ) -> ConnectedOutputState:
        if playback_state == "idle":
            self._playback_state_by_device.pop(device_id, None)
        else:
            self._playback_state_by_device[device_id] = playback_state
        return self.snapshot()

    def snapshot(self) -> ConnectedOutputState:
        if not self._connections:
            return ConnectedOutputState.empty()

        connections = tuple(self._connections.values())
        active = max(connections, key=lambda connection: connection.last_seen_at)
        connected_devices = {connection.device_id for connection in connections}
        return ConnectedOutputState(
            active_device_id=active.device_id,
            audio_target_available=any(
                connection.can_receive_audio for connection in connections
            ),
            display_target_available=any(
                connection.can_receive_display for connection in connections
            ),
            connected_device_count=len(connected_devices),
            connected_connection_count=len(connections),
            playback_state_by_device=dict(self._playback_state_by_device),
            last_presence_at=active.last_seen_at,
        )
