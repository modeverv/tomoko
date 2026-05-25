from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from server.edge.participation.wake_word import WakeWordJudge
from server.edge.pipeline.vad import VADProcessor
from server.gateway.thinking import fast
from server.gateway.thinking.fast import ThinkFastMode
from server.session import TomoroSession
from server.shared.inference.backends.base import InferenceBackend
from server.shared.models import (
    AttentionMode,
    ConversationTurn,
    ParticipationMode,
    SpeechSegment,
    ThinkingEvent,
    ThinkingInput,
    Transcript,
)

ROOT = Path(__file__).resolve().parents[2]


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
        self.transcript = transcript
        self.tomoko_participated = tomoko_participated


class InMemoryConversationLogWriter:
    def __init__(self, history: list[ConversationTurn] | None = None) -> None:
        self.history = history or []
        self.user_turns: list[tuple[Transcript, ParticipationMode]] = []
        self.tomoko_turns: list[tuple[str, str, str]] = []

    async def write_user_turn(
        self,
        transcript: Transcript,
        *,
        participation_mode: ParticipationMode,
    ) -> None:
        self.user_turns.append((transcript, participation_mode))
        self.history.append(
            ConversationTurn(
                speaker="user",
                text=transcript.text,
                timestamp=transcript.recorded_at,
            )
        )

    async def write_tomoko_turn(
        self,
        *,
        text: str,
        emotion: str,
        device_id: str,
        status: str = "completed",
    ) -> None:
        del device_id
        self.tomoko_turns.append((text, emotion, status))
        self.history.append(
            ConversationTurn(
                speaker="tomoko",
                text=text,
                timestamp=datetime.now(UTC),
                emotion=emotion,
            )
        )

    async def read_recent_turns(self, *, limit: int) -> list[ConversationTurn]:
        return self.history[-limit:]


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
def test_base_persona_contains_voice_conversation_rules() -> None:
    prompt = (ROOT / "prompts" / "base_persona.md").read_text(encoding="utf-8")

    assert "音声会話" in prompt
    assert "聞き取れなかった" in prompt
    assert "確認して" in prompt
    assert "開発中のTomoko" in prompt
    assert "EMOTION:<emotion>" in prompt


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
async def test_think_fast_includes_recent_conversation_context(tmp_path) -> None:
    persona = tmp_path / "persona.md"
    persona.write_text("あなたはトモコです。", encoding="utf-8")
    backend = FakeBackend(["うん"])
    mode = ThinkFastMode(persona_path=persona)

    events = [
        event
        async for event in mode.think(
            backend,
            ThinkingInput(
                text="さっき言ったカレーの続きだけど",
                speaker=None,
                context=[
                    ConversationTurn(
                        speaker="user",
                        text="昨日カレーを作ったよ",
                        timestamp=datetime(2026, 5, 24, 9, 0, tzinfo=UTC),
                    ),
                    ConversationTurn(
                        speaker="tomoko",
                        text="いいね、少し寝かせるとおいしいよ。",
                        timestamp=datetime(2026, 5, 24, 9, 1, tzinfo=UTC),
                        emotion="happy",
                    ),
                ],
                emotion="neutral",
                device_id="browser",
            ),
        )
    ]

    assert events[-1] == ThinkingEvent(type="done", value="")
    assert backend.messages == [
        {"role": "user", "content": "昨日カレーを作ったよ"},
        {"role": "assistant", "content": "いいね、少し寝かせるとおいしいよ。"},
        {"role": "user", "content": "さっき言ったカレーの続きだけど"},
    ]


@pytest.mark.unit
async def test_think_fast_logs_llm_prompt_payload(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    persona = tmp_path / "persona.md"
    persona.write_text("あなたはトモコです。", encoding="utf-8")
    backend = FakeBackend(["うん"])
    mode = ThinkFastMode(persona_path=persona)
    log_calls: list[tuple[str, tuple[object, ...]]] = []

    def fake_info(message: str, *args: object) -> None:
        log_calls.append((message, args))

    monkeypatch.setattr(fast.logger, "info", fake_info)

    events = [
        event
        async for event in mode.think(
            backend,
            ThinkingInput(
                text="トモコ、今のプロンプト見せて",
                speaker=None,
                context=[
                    ConversationTurn(
                        speaker="tomoko",
                        text="うん、準備できてるよ。",
                        timestamp=datetime(2026, 5, 25, 10, 0, tzinfo=UTC),
                        emotion="happy",
                    )
                ],
                emotion="neutral",
                device_id="browser",
            ),
        )
    ]

    assert events[-1] == ThinkingEvent(type="done", value="")
    message, args = log_calls[0]
    assert message == "ThinkFastMode llm_prompt backend=%s payload=%s"
    assert args[0] == "fake"
    payload = str(args[1])
    assert '"system_prompt": "あなたはトモコです。"' in payload
    assert '"role": "assistant", "content": "うん、準備できてるよ。"' in payload
    assert '"role": "user", "content": "トモコ、今のプロンプト見せて"' in payload
    assert '"device_id": "browser"' in payload


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
async def test_think_fast_extracts_emotion_prefix_without_newline(tmp_path) -> None:
    persona = tmp_path / "persona.md"
    persona.write_text("あなたはトモコです。", encoding="utf-8")
    backend = FakeBackend(["EMOTION:happy 今日は元気いっぱいだよ！"])
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
        ThinkingEvent(type="text_delta", value="今日は元気いっぱいだよ！"),
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
    await session._wait_for_reply_task()

    assert router.selections == [("conversation", "privacy")]
    assert {"type": "participation", "mode": "called"} in events
    assert {"type": "reply_text", "delta": "うん"} in events
    assert {"type": "reply_text", "delta": "、聞こえるよ"} in events
    assert {"type": "reply_done"} in events
    assert {"type": "state", "state": "idle"} in events


@pytest.mark.unit
async def test_session_passes_recent_conversation_context_to_thinking_mode() -> None:
    events: list[dict[str, str]] = []
    backend = FakeBackend(["うん、覚えてるよ。"])
    router = FakeRouter(backend)
    history = [
        ConversationTurn(
            speaker="user",
            text="昨日カレーを作ったよ",
            timestamp=datetime(2026, 5, 24, 9, 0, tzinfo=UTC),
        ),
        ConversationTurn(
            speaker="tomoko",
            text="明日は少し味がなじむかも。",
            timestamp=datetime(2026, 5, 24, 9, 1, tzinfo=UTC),
            emotion="happy",
        ),
    ]
    conversation_logs = InMemoryConversationLogWriter(history=history)
    session = TomoroSession(
        vad_processor=VADProcessor(vad=SequenceVAD([0.9] + [0.1] * 13), silence_ms=400),
        send_event=events.append,
        transcriber=ConstantTranscriber(),
        participation_judge=WakeWordJudge(),
        ambient_log_writer=InMemoryAmbientLogWriter(),
        conversation_log_writer=conversation_logs,
        router=router,  # type: ignore[arg-type]
        thinking_mode=ThinkFastMode(),
    )

    for _ in range(14):
        await session.process_audio_chunk(np.ones(512, dtype=np.float32).tobytes())
    await session._wait_for_reply_task()

    assert backend.messages == [
        {"role": "user", "content": "昨日カレーを作ったよ"},
        {"role": "assistant", "content": "明日は少し味がなじむかも。"},
        {"role": "user", "content": "トモコ、聞こえる？"},
    ]


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
    await session._wait_for_reply_task()

    assert {
        "type": "emotion",
        "value": "surprised",
        "image": "/assets/images/tomoko-surprised.svg",
    } in events
    assert {"type": "reply_text", "delta": "え、そうなんだ。"} in events
