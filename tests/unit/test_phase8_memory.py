from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from server.gateway.thinking.base import ThinkingMode
from server.gateway.thinking.deep import ThinkDeepMode
from server.gateway.thinking.fast import ThinkFastMode
from server.gateway.thinking.selector import has_calendar_cue, should_use_deep_memory
from server.session import TomoroSession
from server.shared.inference.backends.base import InferenceBackend
from server.shared.memory import NullConversationMemoryStore, _to_vector_literal
from server.shared.models import (
    MemoryHit,
    SessionSummaryHit,
    ThinkingEvent,
    ThinkingInput,
    Transcript,
)

JST = timezone(timedelta(hours=9), "JST")


def fixed_now() -> datetime:
    return datetime(2026, 5, 30, 12, 34, 56, tzinfo=JST)


class FakeBackend(InferenceBackend):
    name = "fake"
    privacy_allowed = True

    def __init__(self) -> None:
        self.system_prompt: str | None = None
        self.messages: list[dict[str, str]] | None = None

    async def chat_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
    ) -> AsyncGenerator[str, None]:
        self.system_prompt = system_prompt
        self.messages = messages
        yield "EMOTION:gentle\n覚えてるよ。"


class FakeEmbeddingBackend:
    name = "fake_e5"
    model = "intfloat/multilingual-e5-small"
    dimensions = 384
    privacy_allowed = True

    async def embed_query(self, text: str) -> list[float]:
        del text
        return [0.1, 0.2, 0.3]

    async def embed_passage(self, text: str) -> list[float]:
        del text
        return [0.4, 0.5, 0.6]


class FakeRouter:
    def __init__(self) -> None:
        self.backend = FakeBackend()

    async def select(self, role: str, preference: str = "privacy") -> InferenceBackend:
        assert role == "conversation"
        assert preference == "privacy"
        return self.backend


class RecordingMode(ThinkingMode):
    def __init__(self) -> None:
        self.inputs: list[ThinkingInput] = []

    async def think(
        self,
        backend: InferenceBackend,
        thinking_input: ThinkingInput,
    ) -> AsyncGenerator[ThinkingEvent, None]:
        del backend
        self.inputs.append(thinking_input)
        yield ThinkingEvent(type="done", value="")


class FakeMemoryStore:
    def __init__(self) -> None:
        self.searches: list[list[float]] = []

    async def search_similar(
        self,
        *,
        embedding: list[float],
        limit: int,
    ) -> list[MemoryHit]:
        self.searches.append(embedding)
        assert limit == 5
        return [
            MemoryHit(
                speaker="user",
                text="トモコ、この前話してたカレーのこと覚えてる？",
                timestamp=datetime(2026, 5, 24, 20, 0, tzinfo=UTC),
                similarity=1.0,
            ),
            MemoryHit(
                speaker="user",
                text="前にカレーの話をした",
                timestamp=datetime(2026, 5, 20, 20, 0, tzinfo=UTC),
                similarity=0.9,
            )
        ]

    async def write_embedding(self, **kwargs) -> None:
        del kwargs

    async def embed_missing_turns(self, **kwargs) -> int:
        del kwargs
        return 0


class FakeSessionSummaryStore:
    def __init__(self) -> None:
        self.searches: list[list[float]] = []
        self.session_id = uuid4()

    async def search_similar_summaries(
        self,
        *,
        embedding: list[float],
        limit: int,
    ) -> list[SessionSummaryHit]:
        self.searches.append(embedding)
        assert limit == 3
        return [
            SessionSummaryHit(
                session_id=self.session_id,
                summary_text="カレーの材料とスパイスの買い物について話した。",
                started_at=datetime(2026, 5, 21, 20, 0, tzinfo=UTC),
                ended_at=datetime(2026, 5, 21, 20, 5, tzinfo=UTC),
                similarity=0.95,
            )
        ]

    async def claim_pending_sessions(self, **kwargs) -> list[object]:
        raise AssertionError("online TomoroSession must not claim summaries")

    async def read_session_turns(self, **kwargs) -> list[object]:
        raise AssertionError("online TomoroSession must not summarize sessions")

    async def complete_summary(self, **kwargs) -> None:
        raise AssertionError("online TomoroSession must not complete summaries")

    async def mark_summary_error(self, **kwargs) -> None:
        raise AssertionError("online TomoroSession must not update summary status")


@pytest.mark.unit
def test_deep_memory_selector_keeps_short_utterances_fast() -> None:
    assert should_use_deep_memory("うん") is False
    assert should_use_deep_memory("トモコ、前に話したカレーのこと覚えてる？") is True
    assert should_use_deep_memory(
        "仕事の進め方について、最近ずっと引っかかっていることを少し整理したい"
    ) is True


@pytest.mark.unit
def test_calendar_cue_is_separate_from_deep_memory_cue() -> None:
    assert has_calendar_cue("今日の予定ある？") is True
    assert has_calendar_cue("明日のスケジュール教えて") is True
    assert has_calendar_cue("今何時") is False
    assert should_use_deep_memory("今日の予定ある？") is False


@pytest.mark.unit
async def test_think_fast_includes_carried_long_term_memory_in_system_prompt(
    tmp_path,
) -> None:
    persona = tmp_path / "persona.md"
    persona.write_text("あなたはトモコです。", encoding="utf-8")
    backend = FakeBackend()
    mode = ThinkFastMode(persona_path=persona, now_provider=fixed_now)

    events = [
        event
        async for event in mode.think(
            backend,
            ThinkingInput(
                text="詳しくはどんな話やったっけ",
                speaker=None,
                context=[],
                emotion="neutral",
                device_id="browser",
                long_term_memory=[
                    MemoryHit(
                        speaker="tomoko",
                        text=(
                            "会話セッション要約: 生成AIと著作権の関係について、"
                            "技術開発者も一種の著作者である可能性を議論した。"
                        ),
                        timestamp=datetime(2026, 5, 27, 0, 8, tzinfo=UTC),
                        similarity=0.84,
                    )
                ],
            ),
        )
    ]

    assert events == [
        ThinkingEvent(type="emotion", value="gentle"),
        ThinkingEvent(type="text_delta", value="覚えてるよ。"),
        ThinkingEvent(type="done", value=""),
    ]
    assert backend.system_prompt is not None
    assert "長期コンテキスト" not in backend.system_prompt
    assert "長期コンテキスト" in backend.messages[-1]["content"]
    assert "生成AIと著作権の関係" in backend.messages[-1]["content"]
    assert backend.messages[-1]["content"].startswith(
        "## CURRENT USER UTTERANCE\n\n詳しくはどんな話やったっけ"
    )


@pytest.mark.unit
async def test_think_fast_keeps_calendar_long_term_memory_compact(tmp_path) -> None:
    persona = tmp_path / "persona.md"
    persona.write_text("あなたはトモコです。", encoding="utf-8")
    backend = FakeBackend()
    mode = ThinkFastMode(persona_path=persona, now_provider=fixed_now)

    [
        event
        async for event in mode.think(
            backend,
            ThinkingInput(
                text="それ詳しく",
                speaker=None,
                context=[],
                emotion="neutral",
                device_id="browser",
                long_term_memory=[
                    MemoryHit(
                        speaker="tomoko",
                        text=(
                            "カレンダー予定: "
                            "2026-05-30 13:00-14:15: 光莉 水泳wear"
                        ),
                        timestamp=datetime(2026, 5, 30, 4, 0, tzinfo=UTC),
                        similarity=1.0,
                        source_id="calendar:gcal:event:2026-05-30T04:00:00+00:00",
                    )
                ],
            ),
        )
    ]

    assert backend.system_prompt is not None
    current_user_message = backend.messages[-1]["content"]
    assert "- 2026-05-30 13:00-14:15: 光莉 水泳wear" in current_user_message
    assert "参照情報: カレンダー" not in current_user_message
    assert "similarity=1.000" not in current_user_message
    assert "[2026-05-30T04:00:00+00:00]" not in current_user_message


@pytest.mark.unit
async def test_think_fast_omits_long_term_memory_block_when_empty(tmp_path) -> None:
    persona = tmp_path / "persona.md"
    persona.write_text("あなたはトモコです。", encoding="utf-8")
    backend = FakeBackend()
    mode = ThinkFastMode(persona_path=persona, now_provider=fixed_now)

    [
        event
        async for event in mode.think(
            backend,
            ThinkingInput(
                text="うん",
                speaker=None,
                context=[],
                emotion="neutral",
                device_id="browser",
            ),
        )
    ]

    assert backend.system_prompt is not None
    assert backend.system_prompt.startswith("あなたはトモコです。")
    assert "現在日時: 2026-05-30 12:34:56 JST" not in backend.system_prompt
    assert "現在日時: 2026-05-30 12:34:56 JST" in backend.messages[-1]["content"]
    assert "曜日: 土曜日" in backend.messages[-1]["content"]


@pytest.mark.unit
async def test_think_deep_includes_long_term_memory_in_system_prompt(tmp_path) -> None:
    persona = tmp_path / "persona.md"
    persona.write_text("あなたはトモコです。", encoding="utf-8")
    backend = FakeBackend()
    mode = ThinkDeepMode(persona_path=persona)

    events = [
        event
        async for event in mode.think(
            backend,
            ThinkingInput(
                text="この前のカレーの続きだけど",
                speaker=None,
                context=[],
                emotion="neutral",
                device_id="browser",
                long_term_memory=[
                    MemoryHit(
                        speaker="user",
                        text="金曜にスパイスカレーを作った",
                        timestamp=datetime(2026, 5, 20, 20, 0, tzinfo=UTC),
                        similarity=0.83,
                    )
                ],
            ),
        )
    ]

    assert events == [
        ThinkingEvent(type="emotion", value="gentle"),
        ThinkingEvent(type="text_delta", value="覚えてるよ。"),
        ThinkingEvent(type="done", value=""),
    ]
    assert backend.system_prompt is not None
    assert "長期コンテキスト" not in backend.system_prompt
    assert "長期コンテキスト" in backend.messages[-1]["content"]
    assert "金曜にスパイスカレーを作った" in backend.messages[-1]["content"]
    assert backend.messages[-1]["content"].startswith(
        "## CURRENT USER UTTERANCE\n\nこの前のカレーの続きだけど"
    )


@pytest.mark.unit
async def test_null_memory_store_keeps_embedding_boundary_noop() -> None:
    store = NullConversationMemoryStore()
    log_id = uuid4()

    await store.write_embedding(
        conversation_log_id=log_id,
        embedding=[0.1, 0.2],
        model="fake",
    )

    assert await store.search_similar(embedding=[0.1, 0.2], limit=3) == []
    assert await store.embed_missing_turns(
        embedding_backend=FakeEmbeddingBackend(),  # type: ignore[arg-type]
    ) == 0


@pytest.mark.unit
def test_pgvector_literal_is_stable() -> None:
    assert _to_vector_literal([0.1, 0.25, -1.0]) == "[0.1,0.25,-1]"


@pytest.mark.unit
async def test_session_uses_deep_mode_when_memory_cue_is_present() -> None:
    events: list[dict[str, str]] = []
    fast = RecordingMode()
    deep = RecordingMode()
    memory_store = FakeMemoryStore()
    session = TomoroSession(
        vad_processor=object(),  # type: ignore[arg-type]
        send_event=events.append,
        router=FakeRouter(),  # type: ignore[arg-type]
        thinking_mode=fast,
        deep_thinking_mode=deep,
        embedding_backend=FakeEmbeddingBackend(),  # type: ignore[arg-type]
        memory_store=memory_store,  # type: ignore[arg-type]
    )

    await session._reply_to(
        Transcript(
            text="トモコ、この前話してたカレーのこと覚えてる？",
            device_id="browser",
            speaker=None,
            audio_level_db=-20.0,
            recorded_at=datetime.now(UTC),
            is_final=True,
        )
    )

    assert fast.inputs == []
    assert len(deep.inputs) == 1
    assert deep.inputs[0].long_term_memory[0].text == "前にカレーの話をした"
    assert memory_store.searches == [[0.1, 0.2, 0.3]]
    assert {"type": "reply_done"} in events


@pytest.mark.unit
async def test_session_summary_hits_are_used_as_deep_memory_without_summarizing() -> None:
    fast = RecordingMode()
    deep = RecordingMode()
    memory_store = FakeMemoryStore()
    summary_store = FakeSessionSummaryStore()
    session = TomoroSession(
        vad_processor=object(),  # type: ignore[arg-type]
        send_event=lambda event: None,
        router=FakeRouter(),  # type: ignore[arg-type]
        thinking_mode=fast,
        deep_thinking_mode=deep,
        embedding_backend=FakeEmbeddingBackend(),  # type: ignore[arg-type]
        memory_store=memory_store,  # type: ignore[arg-type]
        session_summary_store=summary_store,  # type: ignore[arg-type]
    )

    await session._reply_to(
        Transcript(
            text="トモコ、この前話したカレーの材料って覚えてる？",
            device_id="browser",
            speaker=None,
            audio_level_db=-20.0,
            recorded_at=datetime.now(UTC),
            is_final=True,
        )
    )

    assert fast.inputs == []
    assert len(deep.inputs) == 1
    assert deep.inputs[0].long_term_memory[0].text == (
        "会話セッション要約: カレーの材料とスパイスの買い物について話した。"
    )
    assert summary_store.searches == [[0.1, 0.2, 0.3]]
    assert memory_store.searches == [[0.1, 0.2, 0.3]]
