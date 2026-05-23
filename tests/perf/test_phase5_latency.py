from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import numpy as np
import pytest

from server.edge.participation.wake_word import WakeWordJudge
from server.edge.pipeline.vad import VADProcessor
from server.gateway.thinking.fast import ThinkFastMode
from server.session import TomoroSession
from server.shared.inference.backends.base import InferenceBackend
from server.shared.inference.tts.say import SayBackend
from server.shared.models import SpeechSegment, Transcript


class SequenceVAD:
    def __init__(self, scores: list[float]) -> None:
        self.scores = scores
        self.index = 0

    def process_chunk(self, chunk: np.ndarray) -> float:
        score = self.scores[self.index]
        self.index += 1
        return score


class ConstantTranscriber:
    async def transcribe(self, segment: SpeechSegment) -> Transcript:
        return Transcript(
            text="トモコ、聞こえる？",
            device_id=segment.device_id,
            speaker=None,
            audio_level_db=-20.0,
            recorded_at=datetime.now(UTC),
            is_final=True,
        )


class NoopAmbientLogWriter:
    async def write(self, transcript: Transcript, *, tomoko_participated: bool) -> None:
        pass


class FastBackend(InferenceBackend):
    name = "fast"
    privacy_allowed = True

    async def chat_stream(
        self, system_prompt: str, messages: list[dict[str, str]]
    ) -> AsyncGenerator[str, None]:
        yield "うん。"


class FastRouter:
    async def select(self, role: str, preference: str = "privacy") -> InferenceBackend:
        return FastBackend()


@pytest.mark.perf
async def test_e2e_latency_under_800ms_with_say_backend() -> None:
    first_audio_ms: float | None = None
    start = time.perf_counter()

    def record_audio(chunk: bytes) -> None:
        nonlocal first_audio_ms
        if first_audio_ms is None:
            first_audio_ms = (time.perf_counter() - start) * 1000
        assert chunk.startswith(b"RIFF")
        assert b"WAVE" in chunk[:32]

    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.9] + [0.1] * 13), silence_ms=400),
        send_event=lambda event: None,
        send_audio=record_audio,
        transcriber=ConstantTranscriber(),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=NoopAmbientLogWriter(),
        router=FastRouter(),  # type: ignore[arg-type]
        thinking_mode=ThinkFastMode(),
        tts_backend=SayBackend(voice="Kyoko"),
    )

    for _ in range(14):
        await session.process_audio_chunk(np.ones(512, dtype=np.float32).tobytes())

    assert first_audio_ms is not None
    assert first_audio_ms < 800
