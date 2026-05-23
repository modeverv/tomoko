from __future__ import annotations

import struct

import numpy as np
import pytest
from fastapi.testclient import TestClient

from server.edge.main import app
from server.edge.pipeline.vad import VADProcessor


class ConstantVAD:
    def __init__(self, score: float) -> None:
        self.score = score

    def process_chunk(self, chunk: np.ndarray) -> float:
        return self.score


class SequenceVAD:
    def __init__(self, scores: list[float]) -> None:
        self.scores = scores
        self.index = 0

    def process_chunk(self, chunk: np.ndarray) -> float:
        score = self.scores[self.index]
        self.index += 1
        return score


def set_test_vad(processor: VADProcessor) -> None:
    app.state.vad_processor_factory = lambda: processor


@pytest.mark.unit
def test_ws_echoes_float32_binary_without_conversion() -> None:
    set_test_vad(VADProcessor(vad=ConstantVAD(0.0)))
    samples = [0.0, 0.25, -0.5, 1.0]
    payload = struct.pack("<4f", *samples)

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_bytes(payload)

            echoed = websocket.receive_bytes()

    assert echoed == payload


@pytest.mark.unit
def test_ws_echoes_multiple_chunks_in_order() -> None:
    set_test_vad(VADProcessor(vad=ConstantVAD(0.0)))
    first = struct.pack("<3f", 0.1, 0.2, 0.3)
    second = struct.pack("<3f", -0.1, -0.2, -0.3)

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_bytes(first)
            websocket.send_bytes(second)

            assert websocket.receive_bytes() == first
            assert websocket.receive_bytes() == second


@pytest.mark.unit
def test_ws_sends_state_events_on_vad_transitions() -> None:
    set_test_vad(VADProcessor(vad=SequenceVAD([0.9] + [0.1] * 13), silence_ms=400))
    chunk = np.ones(512, dtype=np.float32).tobytes()

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_bytes(chunk)
            assert websocket.receive_json() == {"type": "state", "state": "listening"}
            assert websocket.receive_bytes() == chunk

            for _ in range(12):
                websocket.send_bytes(chunk)
                assert websocket.receive_bytes() == chunk

            websocket.send_bytes(chunk)
            assert websocket.receive_json() == {"type": "state", "state": "processing"}
            assert websocket.receive_bytes() == chunk
