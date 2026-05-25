from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from server.edge.pipeline.stt_filter import TranscriptFilter
from server.edge.pipeline.vad import VADProcessor
from server.edge.remote import EdgeRemoteAudioSession
from server.shared.models import SpeechSegment, Transcript


class SequenceVAD:
    def __init__(self, scores: list[float]) -> None:
        self.scores = scores
        self.index = 0

    def process_chunk(self, chunk: np.ndarray) -> float:
        del chunk
        score = self.scores[self.index]
        self.index += 1
        return score


class FakeTranscriber:
    def __init__(self) -> None:
        self.segments: list[SpeechSegment] = []

    async def transcribe(self, segment: SpeechSegment) -> Transcript:
        self.segments.append(segment)
        return Transcript(
            text="ご視聴ありがとうございました",
            device_id=segment.device_id,
            speaker=None,
            audio_level_db=-60.0,
            recorded_at=datetime.now(UTC),
            is_final=True,
        )


@pytest.mark.unit
async def test_edge_remote_rejects_low_signal_segment_before_stt() -> None:
    browser_events: list[dict[str, object]] = []
    gateway_events: list[dict[str, object]] = []
    transcriber = FakeTranscriber()
    session = EdgeRemoteAudioSession(
        device_id="kitchen",
        vad_processor=VADProcessor(
            vad=SequenceVAD([0.9] + [0.1] * 13),
            silence_ms=400,
            device_id="kitchen",
        ),
        transcriber=transcriber,
        transcript_filter=TranscriptFilter(),
        send_browser_event=browser_events.append,
        send_gateway_event=gateway_events.append,
    )

    for _ in range(14):
        await session.process_audio_chunk(
            (np.ones(512, dtype=np.float32) * 0.001).tobytes()
        )

    assert transcriber.segments == []
    assert gateway_events == []
    assert browser_events[-1] == {"type": "state", "state": "idle"}
