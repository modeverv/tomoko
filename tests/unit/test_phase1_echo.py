from __future__ import annotations

import struct

import numpy as np
import pytest
from fastapi import WebSocketDisconnect

from server.edge.main import app, websocket_session
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
    app.state.transcriber_factory = lambda: None
    app.state.ambient_log_writer_factory = lambda: None


@pytest.mark.unit
async def test_ws_consumes_float32_binary_without_echoing_it() -> None:
    set_test_vad(VADProcessor(vad=ConstantVAD(0.0)))
    samples = [0.0, 0.25, -0.5, 1.0]
    payload = struct.pack("<4f", *samples)
    websocket = FakeWebSocket([payload])

    await websocket_session(websocket)  # type: ignore[arg-type]

    assert websocket.accepted is True
    assert websocket.sent_bytes == []


@pytest.mark.unit
async def test_ws_sends_state_events_on_vad_transitions() -> None:
    set_test_vad(VADProcessor(vad=SequenceVAD([0.9] + [0.1] * 13), silence_ms=400))
    chunk = np.ones(512, dtype=np.float32).tobytes()
    websocket = FakeWebSocket([chunk] * 14)

    await websocket_session(websocket)  # type: ignore[arg-type]

    assert websocket.sent_json == [
        {"type": "state", "state": "listening"},
        {"type": "state", "state": "processing"},
    ]
    assert websocket.sent_bytes == []


class FakeWebSocket:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.accepted = False
        self.sent_json: list[dict[str, str]] = []
        self.sent_bytes: list[bytes] = []

    async def accept(self) -> None:
        self.accepted = True

    async def receive_bytes(self) -> bytes:
        if not self.chunks:
            raise WebSocketDisconnect()
        return self.chunks.pop(0)

    async def send_json(self, event: dict[str, str]) -> None:
        self.sent_json.append(event)

    async def send_bytes(self, chunk: bytes) -> None:
        self.sent_bytes.append(chunk)
