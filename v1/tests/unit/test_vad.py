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
def test_vad_processor_includes_pre_roll_at_segment_head() -> None:
    processor = VADProcessor(
        vad=SequenceVAD([0.1, 0.1, 0.9, 0.1]),
        silence_ms=100,
        sample_rate=1000,
        pre_roll_ms=300,
    )

    chunks = [
        np.full(100, 1.0, dtype=np.float32),
        np.full(100, 2.0, dtype=np.float32),
        np.full(100, 9.0, dtype=np.float32),
        np.full(100, 0.0, dtype=np.float32),
    ]
    results = [processor.process_chunk(chunk) for chunk in chunks]

    segment = results[-1].segment
    assert segment is not None
    np.testing.assert_array_equal(
        segment.audio,
        np.concatenate(chunks),
    )


@pytest.mark.unit
def test_vad_processor_trims_pre_roll_to_sample_limit() -> None:
    processor = VADProcessor(
        vad=SequenceVAD([0.1, 0.1, 0.1, 0.9, 0.1]),
        silence_ms=100,
        sample_rate=1000,
        pre_roll_ms=200,
    )

    chunks = [
        np.full(100, 1.0, dtype=np.float32),
        np.full(100, 2.0, dtype=np.float32),
        np.full(100, 3.0, dtype=np.float32),
        np.full(100, 9.0, dtype=np.float32),
        np.full(100, 0.0, dtype=np.float32),
    ]
    results = [processor.process_chunk(chunk) for chunk in chunks]

    segment = results[-1].segment
    assert segment is not None
    np.testing.assert_array_equal(
        segment.audio,
        np.concatenate(chunks[1:]),
    )


@pytest.mark.unit
def test_vad_processor_reset_clears_pre_roll() -> None:
    processor = VADProcessor(
        vad=SequenceVAD([0.1, 0.9, 0.1]),
        silence_ms=100,
        sample_rate=1000,
        pre_roll_ms=300,
    )

    processor.process_chunk(np.full(100, 1.0, dtype=np.float32))
    processor.reset()
    results = [
        processor.process_chunk(np.full(100, 9.0, dtype=np.float32)),
        processor.process_chunk(np.full(100, 0.0, dtype=np.float32)),
    ]

    segment = results[-1].segment
    assert segment is not None
    np.testing.assert_array_equal(
        segment.audio,
        np.concatenate(
            [
                np.full(100, 9.0, dtype=np.float32),
                np.full(100, 0.0, dtype=np.float32),
            ]
        ),
    )


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
