from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from server.edge.participation.wake_word import WakeWordJudge
from server.edge.pipeline.vad import VADProcessor
from server.session import TomoroSession
from server.shared.models import AttentionMode, ParticipationMode, SpeechSegment, Transcript


class SequenceVAD:
    def __init__(self, scores: list[float]) -> None:
        self.scores = scores
        self.index = 0

    def process_chunk(self, chunk: np.ndarray) -> float:
        score = self.scores[self.index]
        self.index += 1
        return score


class ConstantTranscriber:
    def __init__(self, text: str) -> None:
        self.text = text
        self.segments: list[SpeechSegment] = []

    async def transcribe(self, segment: SpeechSegment) -> Transcript:
        self.segments.append(segment)
        return Transcript(
            text=self.text,
            device_id=segment.device_id,
            speaker=None,
            audio_level_db=-20.0,
            recorded_at=datetime.now(UTC),
            is_final=True,
        )


class StreamingTranscriber(ConstantTranscriber):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.reset_count = 0
        self.partial_sent = False

    async def process_stream_chunk(
        self,
        chunk: np.ndarray,
        *,
        device_id: str,
        sample_rate: int,
    ) -> Transcript | None:
        del chunk, sample_rate
        if self.partial_sent:
            return None
        self.partial_sent = True
        return Transcript(
            text="途中です",
            device_id=device_id,
            speaker=None,
            audio_level_db=-20.0,
            recorded_at=datetime.now(UTC),
            is_final=False,
        )

    def reset_stream(self) -> None:
        self.reset_count += 1


class InMemoryAmbientLogWriter:
    def __init__(self) -> None:
        self.rows: list[tuple[Transcript, bool]] = []

    async def write(
        self,
        transcript: Transcript,
        *,
        tomoko_participated: bool,
        attention_mode: AttentionMode,
        attended: bool,
        participation_mode: ParticipationMode,
    ) -> None:
        del attention_mode, attended, participation_mode
        self.rows.append((transcript, tomoko_participated))


@pytest.mark.unit
async def test_session_transcribes_and_logs_all_finished_speech() -> None:
    events: list[dict[str, str]] = []
    transcriber = ConstantTranscriber("今日いい天気だね")
    ambient_logs = InMemoryAmbientLogWriter()
    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.9] + [0.1] * 13), silence_ms=400),
        send_event=events.append,
        transcriber=transcriber,
        participation_judge=WakeWordJudge(),
        ambient_log_writer=ambient_logs,
    )

    for _ in range(14):
        await session.process_audio_chunk(np.ones(512, dtype=np.float32).tobytes())

    assert len(transcriber.segments) == 1
    assert len(ambient_logs.rows) == 1
    transcript, participated = ambient_logs.rows[0]
    assert transcript.text == "今日いい天気だね"
    assert participated is False
    assert {"type": "participation", "mode": "called"} not in events


@pytest.mark.unit
async def test_session_emits_participation_event_for_wake_word() -> None:
    events: list[dict[str, str]] = []
    ambient_logs = InMemoryAmbientLogWriter()
    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.9] + [0.1] * 13), silence_ms=400),
        send_event=events.append,
        transcriber=ConstantTranscriber("トモコ、聞こえる？"),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=ambient_logs,
    )

    for _ in range(14):
        await session.process_audio_chunk(np.ones(512, dtype=np.float32).tobytes())

    assert ambient_logs.rows[0][1] is True
    assert {"type": "participation", "mode": "called"} in events
    assert events[-1] == {"type": "state", "state": "idle"}


@pytest.mark.unit
async def test_session_emits_streaming_partial_transcript() -> None:
    events: list[dict[str, str]] = []
    transcriber = StreamingTranscriber("今日いい天気だね")
    ambient_logs = InMemoryAmbientLogWriter()
    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.9] + [0.1] * 13), silence_ms=400),
        send_event=events.append,
        transcriber=transcriber,
        participation_judge=WakeWordJudge(),
        ambient_log_writer=ambient_logs,
    )

    for _ in range(14):
        await session.process_audio_chunk(np.ones(512, dtype=np.float32).tobytes())

    assert {"type": "transcript_partial", "text": "途中です"} in events
    assert transcriber.reset_count == 1
