from __future__ import annotations

import struct

import pytest
from fastapi.testclient import TestClient

from server.edge.main import app


@pytest.mark.unit
def test_ws_echoes_float32_binary_without_conversion() -> None:
    samples = [0.0, 0.25, -0.5, 1.0]
    payload = struct.pack("<4f", *samples)

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_bytes(payload)

            echoed = websocket.receive_bytes()

    assert echoed == payload


@pytest.mark.unit
def test_ws_echoes_multiple_chunks_in_order() -> None:
    first = struct.pack("<3f", 0.1, 0.2, 0.3)
    second = struct.pack("<3f", -0.1, -0.2, -0.3)

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_bytes(first)
            websocket.send_bytes(second)

            assert websocket.receive_bytes() == first
            assert websocket.receive_bytes() == second
