from __future__ import annotations

import json
import struct

import numpy as np
import pytest
from fastapi import WebSocketDisconnect

from server.edge.debug_recording import DebugAudioRecorder
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
    app.state.conversation_log_writer_factory = lambda: None
    app.state.tts_backend_factory = lambda: None
    if hasattr(app.state, "debug_recorder_factory"):
        del app.state.debug_recorder_factory


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

    state_events = [
        event for event in websocket.sent_json if event.get("type") == "state"
    ]
    assert state_events == [
        {"type": "state", "state": "listening"},
        {"type": "state", "state": "processing"},
    ]
    assert websocket.sent_bytes == []


@pytest.mark.unit
async def test_ws_accepts_playback_telemetry_text_events() -> None:
    set_test_vad(VADProcessor(vad=ConstantVAD(0.0)))
    websocket = FakeWebSocket(
        [
            json.dumps(
                {
                    "type": "playback_started",
                    "turn_id": "turn-1",
                    "chunk_id": 3,
                    "scheduled_audio_time": 1.2,
                    "sent_audio_time": 1.1,
                    "audio_context_time": 1.25,
                    "performance_now_ms": 100.0,
                }
            )
        ]
    )

    await websocket_session(websocket)  # type: ignore[arg-type]

    assert websocket.accepted is True
    assert websocket.sent_bytes == []


@pytest.mark.unit
async def test_ws_debug_recording_saves_audio_without_session_processing(
    tmp_path,
) -> None:
    processor = VADProcessor(vad=ConstantVAD(0.9))
    set_test_vad(processor)
    app.state.debug_recorder_factory = lambda: DebugAudioRecorder(
        root=tmp_path,
        transcriber=None,
    )
    chunk = np.ones(512, dtype=np.float32).tobytes()
    websocket = FakeWebSocket(
        [
            json.dumps(
                {
                    "type": "debug_recording_start",
                    "kind": "noise",
                    "duration_ms": 32,
                }
            ),
            chunk,
        ]
    )

    await websocket_session(websocket)  # type: ignore[arg-type]

    debug_events = [
        event
        for event in websocket.sent_json
        if str(event.get("type")).startswith("debug_recording_")
    ]
    assert debug_events[0]["type"] == "debug_recording_started"
    assert debug_events[1]["type"] == "debug_recording_saved"
    assert debug_events[1]["kind"] == "noise"
    assert debug_events[1]["sample_count"] == 512
    assert processor.state == "idle"
    assert list((tmp_path / "audio-recordings").glob("*.wav"))
    assert list((tmp_path / "audio-recordings").glob("*.json"))


class FakeWebSocket:
    def __init__(self, messages: list[bytes | str]) -> None:
        self.messages = messages
        self.accepted = False
        self.sent_json: list[dict[str, object]] = []
        self.sent_bytes: list[bytes] = []

    async def accept(self) -> None:
        self.accepted = True

    async def receive(self) -> dict[str, object]:
        if not self.messages:
            raise WebSocketDisconnect()
        message = self.messages.pop(0)
        if isinstance(message, bytes):
            return {"type": "websocket.receive", "bytes": message}
        return {"type": "websocket.receive", "text": message}

    async def send_json(self, event: dict[str, object]) -> None:
        self.sent_json.append(event)

    async def send_bytes(self, chunk: bytes) -> None:
        self.sent_bytes.append(chunk)
