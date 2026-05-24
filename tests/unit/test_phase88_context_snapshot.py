from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from server.gateway.context import ContextSnapshotBuilder
from server.session import TomoroSession
from server.shared.models import (
    ContextBuildPolicy,
    ConversationTurn,
    MemoryHit,
    PersonaLexiconSnapshot,
    PersonaStateSnapshot,
    SessionSummaryHit,
    ThinkingInput,
    Transcript,
)


class InMemoryConversationReader:
    def __init__(
        self,
        *,
        same_session_turns: list[ConversationTurn] | None = None,
        recent_turns: list[ConversationTurn] | None = None,
        delay_sec: float = 0.0,
    ) -> None:
        self.same_session_turns = same_session_turns or []
        self.recent_turns = recent_turns or []
        self.delay_sec = delay_sec

    async def read_recent_turns(self, *, limit: int) -> list[ConversationTurn]:
        if self.delay_sec:
            await asyncio.sleep(self.delay_sec)
        return self.recent_turns[-limit:]

    async def read_recent_turns_for_session(
        self,
        *,
        conversation_session_id: UUID,
        limit: int,
    ) -> list[ConversationTurn]:
        del conversation_session_id
        if self.delay_sec:
            await asyncio.sleep(self.delay_sec)
        return self.same_session_turns[-limit:]


class CountingConversationReader(InMemoryConversationReader):
    def __init__(
        self,
        *,
        recent_turns: list[ConversationTurn],
    ) -> None:
        super().__init__(recent_turns=recent_turns)
        self.recent_calls = 0

    async def read_recent_turns(self, *, limit: int) -> list[ConversationTurn]:
        self.recent_calls += 1
        return await super().read_recent_turns(limit=limit)


class FakeEmbeddingBackend:
    name = "fake_e5"
    model = "fake_e5"
    dimensions = 3
    privacy_allowed = True

    async def embed_query(self, text: str) -> list[float]:
        del text
        return [0.1, 0.2, 0.3]

    async def embed_passage(self, text: str) -> list[float]:
        del text
        return [0.4, 0.5, 0.6]


class FakeMemoryStore:
    async def search_similar(
        self,
        *,
        embedding: list[float],
        limit: int,
    ) -> list[MemoryHit]:
        assert embedding == [0.1, 0.2, 0.3]
        assert limit == 5
        return [
            MemoryHit(
                speaker="user",
                text="トモコ、この前のカレー覚えてる？",
                timestamp=datetime(2026, 5, 23, 21, 0, tzinfo=UTC),
                similarity=1.0,
            ),
            MemoryHit(
                speaker="tomoko",
                text="スパイスはクミンの話をしていたよ。",
                timestamp=datetime(2026, 5, 22, 21, 0, tzinfo=UTC),
                similarity=0.91,
            ),
        ]

    async def write_embedding(self, **kwargs) -> None:
        del kwargs

    async def embed_missing_turns(self, **kwargs) -> int:
        del kwargs
        return 0


class FakeSummaryStore:
    def __init__(self) -> None:
        self.session_id = uuid4()

    async def search_similar_summaries(
        self,
        *,
        embedding: list[float],
        limit: int,
    ) -> list[SessionSummaryHit]:
        assert embedding == [0.1, 0.2, 0.3]
        assert limit == 3
        return [
            SessionSummaryHit(
                session_id=self.session_id,
                summary_text="カレーの材料と買い物について話した。",
                started_at=datetime(2026, 5, 22, 20, 0, tzinfo=UTC),
                ended_at=datetime(2026, 5, 22, 20, 30, tzinfo=UTC),
                similarity=0.94,
            )
        ]


class FakePersonaStore:
    async def read_latest_lexicon(self) -> PersonaLexiconSnapshot:
        return PersonaLexiconSnapshot.from_json(
            {
                "schema_version": 1,
                "user_terms": [
                    {
                        "term": "カレー",
                        "meaning": "週末によく作る料理",
                        "salience": 0.9,
                        "tone": "warm",
                    },
                    {
                        "term": "散歩",
                        "meaning": "気分転換",
                        "salience": 0.4,
                    },
                ],
            }
        )

    async def read_latest_state(self) -> PersonaStateSnapshot:
        return PersonaStateSnapshot.from_json(
            {
                "schema_version": 1,
                "traits": {"curiosity": 0.8},
                "relationship": {"familiarity": 0.7, "preferred_address": "きみ"},
                "speaking_style": {
                    "sentence_length": "short",
                    "honorific_level": "casual",
                    "signature_phrases": ["うん"],
                },
            }
        )


class SlowPersonaStore(FakePersonaStore):
    async def read_latest_lexicon(self) -> PersonaLexiconSnapshot:
        await asyncio.sleep(0.05)
        return await super().read_latest_lexicon()

    async def read_latest_state(self) -> PersonaStateSnapshot:
        await asyncio.sleep(0.05)
        return await super().read_latest_state()


@pytest.mark.unit
async def test_fast_snapshot_prefers_same_session_then_supplements_recent() -> None:
    old_turn = ConversationTurn(
        speaker="user",
        text="前の会話の話",
        timestamp=datetime(2026, 5, 24, 8, 0, tzinfo=UTC),
    )
    same_turn = ConversationTurn(
        speaker="tomoko",
        text="今の会話の続き",
        timestamp=datetime(2026, 5, 24, 9, 0, tzinfo=UTC),
    )
    current_turn = ConversationTurn(
        speaker="user",
        text="トモコ、さっきの続き",
        timestamp=datetime(2026, 5, 24, 9, 1, tzinfo=UTC),
    )
    builder = ContextSnapshotBuilder(
        conversation_log_reader=InMemoryConversationReader(
            same_session_turns=[same_turn, current_turn],
            recent_turns=[old_turn, same_turn, current_turn],
        )
    )

    snapshot = await builder.build(
        text=current_turn.text,
        speaker=None,
        device_id="local",
        active_session_id=uuid4(),
        policy=ContextBuildPolicy.for_depth("fast"),
    )

    assert [turn.text for turn in snapshot.recent_turns] == [
        "前の会話の話",
        "今の会話の続き",
    ]
    assert snapshot.depth == "fast"
    assert snapshot.trace.included_counts["recent_turns"] == 2
    assert snapshot.trace.timed_out is False


@pytest.mark.unit
async def test_deep_snapshot_reads_summaries_and_turn_memory() -> None:
    builder = ContextSnapshotBuilder(
        conversation_log_reader=InMemoryConversationReader(),
        embedding_backend=FakeEmbeddingBackend(),  # type: ignore[arg-type]
        memory_store=FakeMemoryStore(),  # type: ignore[arg-type]
        session_summary_store=FakeSummaryStore(),  # type: ignore[arg-type]
    )

    snapshot = await builder.build(
        text="トモコ、この前のカレー覚えてる？",
        speaker=None,
        device_id="local",
        active_session_id=None,
        policy=ContextBuildPolicy.for_depth("deep"),
    )

    assert [hit.summary_text for hit in snapshot.session_summaries] == [
        "カレーの材料と買い物について話した。"
    ]
    assert [hit.text for hit in snapshot.memory_hits] == [
        "スパイスはクミンの話をしていたよ。"
    ]
    assert snapshot.trace.included_counts["session_summaries"] == 1
    assert snapshot.trace.included_counts["memory_hits"] == 1


@pytest.mark.unit
async def test_normal_snapshot_uses_persona_subset_dto() -> None:
    builder = ContextSnapshotBuilder(
        persona_store=FakePersonaStore(),  # type: ignore[arg-type]
    )

    snapshot = await builder.build(
        text="カレーの続き",
        speaker=None,
        device_id="local",
        active_session_id=None,
        policy=ContextBuildPolicy.for_depth("normal"),
    )

    assert [(term.term, term.meaning) for term in snapshot.lexicon_terms] == [
        ("カレー", "週末によく作る料理"),
        ("散歩", "気分転換"),
    ]
    assert snapshot.persona_slice is not None
    assert snapshot.persona_slice.preferred_address == "きみ"
    assert snapshot.persona_slice.signature_phrases == ["うん"]


@pytest.mark.unit
async def test_timeout_returns_degraded_snapshot_with_trace() -> None:
    builder = ContextSnapshotBuilder(
        conversation_log_reader=InMemoryConversationReader(delay_sec=0.05)
    )

    snapshot = await builder.build(
        text="トモコ",
        speaker=None,
        device_id="local",
        active_session_id=uuid4(),
        policy=ContextBuildPolicy(
            depth="fast",
            max_build_ms=1,
            max_prompt_tokens=100,
            max_same_session_turns=2,
            max_recent_turns=2,
            max_session_summaries=0,
            max_memory_hits=0,
            max_lexicon_terms=0,
            allow_turn_memory_search=False,
            allow_persona_slice=False,
        ),
    )

    assert snapshot.recent_turns == []
    assert snapshot.trace.timed_out is True
    assert snapshot.trace.skipped_sources == ["recent_turns", "same_session_turns"]
    assert snapshot.trace.cache_hits["recent_turns"] is False
    assert snapshot.trace.cache_entries["recent_turns"].age_ms is None


@pytest.mark.unit
async def test_recent_turns_cache_records_hit_age_and_ttl() -> None:
    first_turn = ConversationTurn(
        speaker="user",
        text="最初の話",
        timestamp=datetime(2026, 5, 24, 10, 0, tzinfo=UTC),
    )
    reader = CountingConversationReader(recent_turns=[first_turn])
    builder = ContextSnapshotBuilder(
        conversation_log_reader=reader,
        cache_ttl_ms={"recent_turns": 30},
    )
    policy = ContextBuildPolicy.for_depth("fast")

    first_snapshot = await builder.build(
        text="トモコ",
        speaker=None,
        device_id="local",
        active_session_id=None,
        policy=policy,
    )
    second_snapshot = await builder.build(
        text="トモコ",
        speaker=None,
        device_id="local",
        active_session_id=None,
        policy=policy,
    )

    assert reader.recent_calls == 1
    assert first_snapshot.trace.cache_hits["recent_turns"] is False
    assert second_snapshot.trace.cache_hits["recent_turns"] is True
    assert second_snapshot.trace.cache_entries["recent_turns"].age_ms is not None
    assert second_snapshot.trace.cache_entries["recent_turns"].ttl_ms == 30


@pytest.mark.unit
async def test_expired_recent_turns_cache_falls_back_to_reader() -> None:
    first_turn = ConversationTurn(
        speaker="user",
        text="古い cache",
        timestamp=datetime(2026, 5, 24, 10, 0, tzinfo=UTC),
    )
    refreshed_turn = ConversationTurn(
        speaker="tomoko",
        text="DB から読み直した話",
        timestamp=datetime(2026, 5, 24, 10, 1, tzinfo=UTC),
    )
    reader = CountingConversationReader(recent_turns=[first_turn])
    builder = ContextSnapshotBuilder(
        conversation_log_reader=reader,
        cache_ttl_ms={"recent_turns": 1},
    )
    policy = ContextBuildPolicy.for_depth("fast")

    await builder.build(
        text="トモコ",
        speaker=None,
        device_id="local",
        active_session_id=None,
        policy=policy,
    )
    await asyncio.sleep(0.002)
    reader.recent_turns = [refreshed_turn]
    refreshed_snapshot = await builder.build(
        text="トモコ",
        speaker=None,
        device_id="local",
        active_session_id=None,
        policy=policy,
    )

    assert reader.recent_calls == 2
    assert [turn.text for turn in refreshed_snapshot.recent_turns] == [
        "DB から読み直した話"
    ]
    assert refreshed_snapshot.trace.cache_hits["recent_turns"] is False


@pytest.mark.unit
async def test_slow_optional_persona_source_times_out_without_blocking_recent_turns() -> None:
    recent_turn = ConversationTurn(
        speaker="tomoko",
        text="今の話は残す",
        timestamp=datetime(2026, 5, 24, 10, 0, tzinfo=UTC),
    )
    builder = ContextSnapshotBuilder(
        conversation_log_reader=InMemoryConversationReader(recent_turns=[recent_turn]),
        persona_store=SlowPersonaStore(),  # type: ignore[arg-type]
    )

    snapshot = await builder.build(
        text="カレーの続き",
        speaker=None,
        device_id="local",
        active_session_id=None,
        policy=ContextBuildPolicy(
            depth="normal",
            max_build_ms=1,
            max_prompt_tokens=100,
            max_same_session_turns=2,
            max_recent_turns=2,
            max_session_summaries=0,
            max_memory_hits=0,
            max_lexicon_terms=2,
            allow_turn_memory_search=False,
            allow_persona_slice=True,
        ),
    )

    assert snapshot.recent_turns == [recent_turn]
    assert snapshot.trace.timed_out is True
    assert "lexicon_terms" in snapshot.trace.skipped_sources
    assert "persona_slice" in snapshot.trace.skipped_sources
    assert snapshot.trace.cache_hits["lexicon_terms"] is False
    assert snapshot.trace.cache_entries["lexicon_terms"].age_ms is None


@pytest.mark.unit
async def test_tomoro_session_passes_context_snapshot_to_thinking_input() -> None:
    transcript = Transcript(
        text="トモコ、さっきの続き",
        device_id="local",
        speaker=None,
        audio_level_db=-20.0,
        recorded_at=datetime(2026, 5, 24, 9, 1, tzinfo=UTC),
        is_final=True,
    )
    turn = ConversationTurn(
        speaker="tomoko",
        text="今の会話の続き",
        timestamp=datetime(2026, 5, 24, 9, 0, tzinfo=UTC),
    )
    mode = RecordingThinkingMode()
    session = TomoroSession(
        vad_processor=FakeVADProcessor(),  # type: ignore[arg-type]
        send_event=lambda event: None,
        router=FakeRouter(),  # type: ignore[arg-type]
        thinking_mode=mode,
        context_snapshot_builder=ContextSnapshotBuilder(
            conversation_log_reader=InMemoryConversationReader(
                same_session_turns=[turn],
                recent_turns=[turn],
            )
        ),
    )
    session.active_conversation_session_id = uuid4()

    await session._reply_to(transcript)
    await session._wait_for_reply_task()

    assert mode.inputs
    thinking_input = mode.inputs[0]
    assert thinking_input.context == [turn]
    assert thinking_input.context_snapshot is not None
    assert thinking_input.context_snapshot.recent_turns == [turn]


class FakeVADProcessor:
    device_id = "local"
    sample_rate = 16000


class FakeRouter:
    async def select(self, role: str, preference: str):
        del role, preference
        return FakeBackend()


class RecordingThinkingMode:
    def __init__(self) -> None:
        self.inputs: list[ThinkingInput] = []

    async def think(self, backend, thinking_input: ThinkingInput):
        del backend
        self.inputs.append(thinking_input)
        from server.shared.models import ThinkingEvent

        yield ThinkingEvent(type="done", value="")


class FakeBackend:
    name = "fake"
