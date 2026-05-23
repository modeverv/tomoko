from __future__ import annotations

import numpy as np
import pytest

from server.edge.pipeline.vad import VADProcessor
from server.session import TomoroSession


class SequenceVAD:
    def __init__(self, scores: list[float]) -> None:
        self.scores = scores
        self.index = 0

    def process_chunk(self, chunk: np.ndarray) -> float:
        score = self.scores[self.index]
        self.index += 1
        return score


@pytest.mark.unit
def test_vad_processor_starts_listening_on_speech() -> None:
    processor = VADProcessor(vad=SequenceVAD([0.9]), silence_ms=400)

    result = processor.process_chunk(np.ones(512, dtype=np.float32))

    assert result.state_changed_to == "listening"
    assert result.segment is None


@pytest.mark.unit
def test_vad_processor_finishes_after_400ms_silence() -> None:
    processor = VADProcessor(vad=SequenceVAD([0.9] + [0.1] * 13), silence_ms=400)

    results = [processor.process_chunk(np.ones(512, dtype=np.float32)) for _ in range(14)]

    assert results[-1].state_changed_to == "processing"
    assert results[-1].segment is not None
    assert results[-1].segment.audio.dtype == np.float32
    assert len(results[-1].segment.audio) == 512 * 14


@pytest.mark.unit
async def test_tomoro_session_emits_state_transitions() -> None:
    sent: list[dict[str, str]] = []
    processor = VADProcessor(vad=SequenceVAD([0.9] + [0.1] * 13), silence_ms=400)
    session = TomoroSession(vad_processor=processor, send_event=sent.append)

    for _ in range(14):
        await session.process_audio_chunk(np.ones(512, dtype=np.float32).tobytes())

    assert sent == [
        {"type": "state", "state": "listening"},
        {"type": "state", "state": "processing"},
    ]
    assert session.state == "processing"
