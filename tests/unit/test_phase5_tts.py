from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import numpy as np
import pytest

from server.edge.participation.wake_word import WakeWordJudge
from server.edge.pipeline.vad import VADProcessor
from server.gateway.thinking.fast import ThinkFastMode
from server.session import TomoroSession
from server.shared.inference.backends.base import InferenceBackend
from server.shared.inference.tts.base import TTSBackend
from server.shared.inference.tts.say import SayBackend
from server.shared.models import AudioChunkOut, SpeechSegment, Transcript, TTSInput


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


class InMemoryAmbientLogWriter:
    async def write(self, transcript: Transcript, *, tomoko_participated: bool) -> None:
        self.transcript = transcript
        self.tomoko_participated = tomoko_participated


class FakeBackend(InferenceBackend):
    name = "fake"
    privacy_allowed = True

    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks

    async def chat_stream(
        self, system_prompt: str, messages: list[dict[str, str]]
    ) -> AsyncGenerator[str, None]:
        for chunk in self.chunks:
            yield chunk


class FakeRouter:
    def __init__(self, backend: InferenceBackend) -> None:
        self.backend = backend

    async def select(self, role: str, preference: str = "privacy") -> InferenceBackend:
        return self.backend


class FakeTTSBackend(TTSBackend):
    name = "fake_tts"

    def __init__(self) -> None:
        self.inputs: list[TTSInput] = []

    async def synthesize(self, tts_input: TTSInput) -> AsyncGenerator[AudioChunkOut, None]:
        self.inputs.append(tts_input)
        yield AudioChunkOut(
            data=f"audio:{tts_input.text}".encode(),
            sequence=0,
            is_last=True,
        )


@pytest.mark.unit
async def test_session_flushes_tts_on_sentence_punctuation() -> None:
    events: list[dict[str, str]] = []
    audio_chunks: list[bytes] = []
    tts = FakeTTSBackend()
    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.9] + [0.1] * 13), silence_ms=400),
        send_event=events.append,
        send_audio=audio_chunks.append,
        transcriber=ConstantTranscriber(),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=InMemoryAmbientLogWriter(),
        router=FakeRouter(FakeBackend(["うん", "。聞こえる", "よ"])),  # type: ignore[arg-type]
        thinking_mode=ThinkFastMode(),
        tts_backend=tts,
    )

    for _ in range(14):
        await session.process_audio_chunk(np.ones(512, dtype=np.float32).tobytes())

    assert [tts_input.text for tts_input in tts.inputs] == ["うん。", "聞こえるよ"]
    assert audio_chunks == [
        "audio:うん。".encode(),
        "audio:聞こえるよ".encode(),
    ]
    assert {"type": "reply_done"} in events


@pytest.mark.unit
async def test_say_backend_invokes_say_and_returns_aiff_bytes(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    class FakeProc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def fake_create_subprocess_exec(*args, **kwargs) -> FakeProc:
        del kwargs
        calls.append(tuple(args))
        output_path = args[args.index("-o") + 1]
        with open(output_path, "wb") as f:
            f.write(b"FORMfake-aiff")
        return FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create_subprocess_exec)
    backend = SayBackend(voice="Kyoko")

    chunks = [
        chunk
        async for chunk in backend.synthesize(TTSInput(text="こんにちは。", style="happy"))
    ]

    assert chunks == [AudioChunkOut(data=b"FORMfake-aiff", sequence=0, is_last=True)]
    assert calls[0][:4] == ("say", "-v", "Kyoko", "-r")
    assert calls[0][4] == "190"
