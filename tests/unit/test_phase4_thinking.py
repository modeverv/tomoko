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
from server.shared.models import SpeechSegment, ThinkingEvent, ThinkingInput, Transcript


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
        self.system_prompt: str | None = None
        self.messages: list[dict[str, str]] | None = None

    async def chat_stream(
        self, system_prompt: str, messages: list[dict[str, str]]
    ) -> AsyncGenerator[str, None]:
        self.system_prompt = system_prompt
        self.messages = messages
        for chunk in self.chunks:
            yield chunk


class FakeRouter:
    def __init__(self, backend: InferenceBackend) -> None:
        self.backend = backend
        self.selections: list[tuple[str, str]] = []

    async def select(self, role: str, preference: str = "privacy") -> InferenceBackend:
        self.selections.append((role, preference))
        return self.backend


@pytest.mark.unit
async def test_think_fast_wraps_streamed_tokens_in_thinking_events(tmp_path) -> None:
    persona = tmp_path / "persona.md"
    persona.write_text("あなたはトモコです。", encoding="utf-8")
    backend = FakeBackend(["うん", "、聞こえるよ"])
    mode = ThinkFastMode(persona_path=persona)

    events = [
        event
        async for event in mode.think(
            backend,
            ThinkingInput(
                text="トモコ、聞こえる？",
                speaker=None,
                context=[],
                emotion="neutral",
                device_id="browser",
            ),
        )
    ]

    assert events == [
        ThinkingEvent(type="text_delta", value="うん"),
        ThinkingEvent(type="text_delta", value="、聞こえるよ"),
        ThinkingEvent(type="done", value=""),
    ]
    assert backend.system_prompt == "あなたはトモコです。"
    assert backend.messages == [{"role": "user", "content": "トモコ、聞こえる？"}]


@pytest.mark.unit
async def test_think_fast_extracts_emotion_line_before_text(tmp_path) -> None:
    persona = tmp_path / "persona.md"
    persona.write_text("あなたはトモコです。", encoding="utf-8")
    backend = FakeBackend(["EMO", "TION:happy\nうん", "、聞こえるよ。"])
    mode = ThinkFastMode(persona_path=persona)

    events = [
        event
        async for event in mode.think(
            backend,
            ThinkingInput(
                text="トモコ、聞こえる？",
                speaker=None,
                context=[],
                emotion="neutral",
                device_id="browser",
            ),
        )
    ]

    assert events == [
        ThinkingEvent(type="emotion", value="happy"),
        ThinkingEvent(type="text_delta", value="うん"),
        ThinkingEvent(type="text_delta", value="、聞こえるよ。"),
        ThinkingEvent(type="done", value=""),
    ]


@pytest.mark.unit
async def test_session_streams_reply_text_after_wake_word() -> None:
    events: list[dict[str, str]] = []
    backend = FakeBackend(["うん", "、聞こえるよ"])
    router = FakeRouter(backend)
    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.9] + [0.1] * 13), silence_ms=400),
        send_event=events.append,
        transcriber=ConstantTranscriber(),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=InMemoryAmbientLogWriter(),
        router=router,  # type: ignore[arg-type]
        thinking_mode=ThinkFastMode(),
    )

    for _ in range(14):
        await session.process_audio_chunk(np.ones(512, dtype=np.float32).tobytes())

    assert router.selections == [("conversation", "privacy")]
    assert {"type": "participation", "mode": "called"} in events
    assert {"type": "reply_text", "delta": "うん"} in events
    assert {"type": "reply_text", "delta": "、聞こえるよ"} in events
    assert {"type": "reply_done"} in events
    assert events[-1] == {"type": "state", "state": "idle"}


@pytest.mark.unit
async def test_session_sends_emotion_event_after_wake_word() -> None:
    events: list[dict[str, str]] = []
    backend = FakeBackend(["EMOTION:surprised\n", "え、そうなんだ。"])
    router = FakeRouter(backend)
    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.9] + [0.1] * 13), silence_ms=400),
        send_event=events.append,
        transcriber=ConstantTranscriber(),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=InMemoryAmbientLogWriter(),
        router=router,  # type: ignore[arg-type]
        thinking_mode=ThinkFastMode(),
    )

    for _ in range(14):
        await session.process_audio_chunk(np.ones(512, dtype=np.float32).tobytes())

    assert {"type": "emotion", "value": "surprised"} in events
    assert {"type": "reply_text", "delta": "え、そうなんだ。"} in events
