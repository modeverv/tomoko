from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from server.gateway.context import ContextSnapshotBuilder
from server.session import TomoroSession
from server.shared.calendar import InMemoryCalendarEventStore
from server.shared.models import (
    CalendarEvent,
    ContextBuildPolicy,
    ConversationTurn,
    MemoryHit,
    PersonaLexiconSnapshot,
    PersonaStateSnapshot,
    ResearchContextHit,
    SessionSummaryHit,
    TaskLedgerEntry,
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


class CountingEmbeddingBackend(FakeEmbeddingBackend):
    def __init__(self, events: list[str] | None = None) -> None:
        self.query_calls = 0
        self.events = events

    async def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        if self.events is not None:
            self.events.append("query_embedding")
        return await super().embed_query(text)


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


class FakeResearchResultStore:
    async def search_similar(
        self,
        *,
        embedding: list[float],
        limit: int,
    ) -> list[ResearchContextHit]:
        assert embedding == [0.1, 0.2, 0.3]
        assert limit == 3
        return [
            ResearchContextHit(
                result_id="research-openai",
                query="今日のOpenAI関連ニュースを短く",
                summary_text="OpenAIに関する外部調査の要約。",
                provider="perplexity",
                fetched_at=datetime(2026, 5, 31, 10, 0, tzinfo=UTC),
                similarity=0.95,
                citation_urls=("https://example.com/openai",),
            )
        ]

    async def embed_missing_turns(self, **kwargs) -> int:
        del kwargs
        return 0


class FakeTaskLedgerStore:
    def __init__(self, entries: list[TaskLedgerEntry]) -> None:
        self.entries = entries
        self.calls: list[int] = []

    async def read_active_tasks(self, *, limit: int) -> list[TaskLedgerEntry]:
        self.calls.append(limit)
        return self.entries[:limit]


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


class RestoringSummaryStore(FakeSummaryStore):
    def __init__(self) -> None:
        super().__init__()
        self.read_calls = 0

    async def read_session_turns(self, *, session_id: UUID) -> list[ConversationTurn]:
        assert session_id == self.session_id
        self.read_calls += 1
        return [
            ConversationTurn(
                speaker="user",
                text="著作権の話では、AIに学習させる入力側の許諾が気になっていた。",
                timestamp=datetime(2026, 5, 22, 20, 1, tzinfo=UTC),
            ),
            ConversationTurn(
                speaker="user",
                text="生成物よりも、作る過程で誰の作品を使ったかを重く見ていた。",
                timestamp=datetime(2026, 5, 22, 20, 2, tzinfo=UTC),
            ),
            ConversationTurn(
                speaker="tomoko",
                text="つまり、結論としては入力の扱いを分けて考えたいんだね。",
                timestamp=datetime(2026, 5, 22, 20, 3, tzinfo=UTC),
            ),
            ConversationTurn(
                speaker="tomoko",
                text="まとめると、ユーザーは許諾と過程を分けて考えていた。",
                timestamp=datetime(2026, 5, 22, 20, 4, tzinfo=UTC),
            ),
        ]


class OrderedSummaryStore(FakeSummaryStore):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events

    async def search_similar_summaries(
        self,
        *,
        embedding: list[float],
        limit: int,
    ) -> list[SessionSummaryHit]:
        self.events.append("session_summaries")
        return await super().search_similar_summaries(
            embedding=embedding,
            limit=limit,
        )


class OrderedMemoryStore(FakeMemoryStore):
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def search_similar(
        self,
        *,
        embedding: list[float],
        limit: int,
    ) -> list[MemoryHit]:
        assert "session_summaries" in self.events
        self.events.append("memory_hits")
        return await super().search_similar(embedding=embedding, limit=limit)


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


def _calendar_event(
    summary: str,
    *,
    start_time: datetime | None = None,
) -> CalendarEvent:
    start_time = start_time or datetime(2026, 5, 30, 4, 0, tzinfo=UTC)
    return CalendarEvent(
        source_id="gcal",
        uid=f"{summary}@example.com",
        summary=summary,
        start_time=start_time,
        end_time=start_time + timedelta(hours=1),
        all_day=False,
        location="Kitchen",
    )


@pytest.mark.unit
async def test_fast_snapshot_uses_same_session_without_recent_supplement() -> None:
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
        "今の会話の続き",
    ]
    assert snapshot.depth == "fast"
    assert snapshot.trace.included_counts["recent_turns"] == 1
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
    assert snapshot.trace.stage_timings_ms["query_embedding"] >= 0


@pytest.mark.unit
async def test_deep_snapshot_reads_calendar_context() -> None:
    calendar_store = InMemoryCalendarEventStore()
    await calendar_store.replace_source_events(
        source_id="gcal",
        events=[_calendar_event("家族の予定")],
    )
    builder = ContextSnapshotBuilder(
        calendar_store=calendar_store,
        now_provider=lambda: datetime(2026, 5, 30, 0, 0, tzinfo=UTC),
    )

    snapshot = await builder.build(
        text="トモコ、今日の予定ある？",
        speaker=None,
        device_id="local",
        active_session_id=None,
        policy=ContextBuildPolicy.for_depth("deep"),
    )

    assert [event.summary for event in snapshot.calendar_events] == ["家族の予定"]
    assert snapshot.trace.included_counts["calendar_events"] == 1
    assert "calendar_events" in snapshot.trace.stage_timings_ms


@pytest.mark.unit
async def test_deep_snapshot_reads_all_future_30_day_calendar_context() -> None:
    now = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    calendar_store = InMemoryCalendarEventStore()
    june_events = [
        _calendar_event(
            f"6月の予定{i:02d}",
            start_time=now + timedelta(hours=9, days=i),
        )
        for i in range(30)
    ]
    await calendar_store.replace_source_events(
        source_id="gcal",
        events=[
            _calendar_event(
                "昨日の予定",
                start_time=now - timedelta(days=1),
            ),
            *june_events,
            _calendar_event(
                "31日後の予定",
                start_time=now + timedelta(days=31),
            ),
        ],
    )
    builder = ContextSnapshotBuilder(
        calendar_store=calendar_store,
        now_provider=lambda: now,
    )

    snapshot = await builder.build(
        text="トモコ、今後の予定ある？",
        speaker=None,
        device_id="local",
        active_session_id=None,
        policy=ContextBuildPolicy.for_depth("deep"),
    )

    assert [event.summary for event in snapshot.calendar_events] == [
        event.summary for event in june_events
    ]
    assert snapshot.trace.included_counts["calendar_events"] == 30


@pytest.mark.unit
async def test_deep_snapshot_reads_research_result_summaries() -> None:
    builder = ContextSnapshotBuilder(
        embedding_backend=FakeEmbeddingBackend(),  # type: ignore[arg-type]
        research_result_store=FakeResearchResultStore(),
    )

    snapshot = await builder.build(
        text="OpenAIについて知ってることある？",
        speaker=None,
        device_id="local",
        active_session_id=None,
        policy=ContextBuildPolicy.for_depth("deep"),
    )

    assert [hit.summary_text for hit in snapshot.research_results] == [
        "OpenAIに関する外部調査の要約。"
    ]
    assert snapshot.trace.included_counts["research_results"] == 1
    assert "research_results" in snapshot.trace.stage_timings_ms


@pytest.mark.unit
async def test_fast_snapshot_does_not_read_research_results() -> None:
    builder = ContextSnapshotBuilder(
        embedding_backend=FakeEmbeddingBackend(),  # type: ignore[arg-type]
        research_result_store=FakeResearchResultStore(),
    )

    snapshot = await builder.build(
        text="OpenAIについて知ってることある？",
        speaker=None,
        device_id="local",
        active_session_id=None,
        policy=ContextBuildPolicy.for_depth("fast"),
    )

    assert snapshot.research_results == []
    assert snapshot.trace.included_counts["research_results"] == 0
    assert "research_results" not in snapshot.trace.stage_timings_ms


@pytest.mark.unit
async def test_fast_snapshot_reads_top_active_task_ledger_entries() -> None:
    entries = [
        TaskLedgerEntry(
            task_id=f"task-{index}",
            title=f"タスク{index}",
            status="active",
            priority=100 - index,
            created_at=datetime(2026, 6, 2, 9, index, tzinfo=UTC),
            updated_at=datetime(2026, 6, 2, 9, index, tzinfo=UTC),
        )
        for index in range(12)
    ]
    store = FakeTaskLedgerStore(entries)
    builder = ContextSnapshotBuilder(task_ledger_store=store)

    snapshot = await builder.build(
        text="今やることある？",
        speaker=None,
        device_id="browser",
        active_session_id=None,
        policy=ContextBuildPolicy.for_depth("fast"),
    )

    assert store.calls == [10]
    assert [task.title for task in snapshot.task_ledger_entries] == [
        f"タスク{index}" for index in range(10)
    ]
    assert snapshot.trace.included_counts["task_ledger"] == 10
    assert "task_ledger" in snapshot.trace.stage_timings_ms


@pytest.mark.unit
async def test_deep_snapshot_can_read_more_active_task_ledger_entries() -> None:
    entries = [
        TaskLedgerEntry(
            task_id=f"task-{index}",
            title=f"深掘りタスク{index}",
            status="active",
            priority=50,
            created_at=datetime(2026, 6, 2, 10, index, tzinfo=UTC),
            updated_at=datetime(2026, 6, 2, 10, index, tzinfo=UTC),
        )
        for index in range(15)
    ]
    store = FakeTaskLedgerStore(entries)
    builder = ContextSnapshotBuilder(task_ledger_store=store)

    snapshot = await builder.build(
        text="残っているタスクを詳しく教えて",
        speaker=None,
        device_id="browser",
        active_session_id=None,
        policy=ContextBuildPolicy.for_depth("deep"),
    )

    assert store.calls == [25]
    assert len(snapshot.task_ledger_entries) == 15
    assert snapshot.trace.included_counts["task_ledger"] == 15


@pytest.mark.unit
async def test_fast_snapshot_does_not_read_calendar_context() -> None:
    calendar_store = InMemoryCalendarEventStore()
    await calendar_store.replace_source_events(
        source_id="gcal",
        events=[_calendar_event("家族の予定")],
    )
    builder = ContextSnapshotBuilder(calendar_store=calendar_store)

    snapshot = await builder.build(
        text="トモコ、聞こえる？",
        speaker=None,
        device_id="local",
        active_session_id=None,
        policy=ContextBuildPolicy.for_depth("fast"),
    )

    assert snapshot.calendar_events == []
    assert snapshot.trace.included_counts["calendar_events"] == 0


@pytest.mark.unit
async def test_deep_snapshot_shares_query_embedding_and_prioritizes_summary() -> None:
    events: list[str] = []
    embedding_backend = CountingEmbeddingBackend(events)
    builder = ContextSnapshotBuilder(
        conversation_log_reader=InMemoryConversationReader(),
        embedding_backend=embedding_backend,  # type: ignore[arg-type]
        memory_store=OrderedMemoryStore(events),  # type: ignore[arg-type]
        session_summary_store=OrderedSummaryStore(events),  # type: ignore[arg-type]
    )

    snapshot = await builder.build(
        text="トモコ、この前のカレー覚えてる？",
        speaker=None,
        device_id="local",
        active_session_id=None,
        policy=ContextBuildPolicy.for_depth("deep"),
    )

    assert embedding_backend.query_calls == 1
    assert events == ["query_embedding", "session_summaries", "memory_hits"]
    assert snapshot.trace.cache_hits["query_embedding"] is False
    assert snapshot.session_summaries
    assert snapshot.memory_hits


@pytest.mark.unit
async def test_summary_hit_restores_user_turn_snippets_with_source_scores() -> None:
    summary_store = RestoringSummaryStore()
    builder = ContextSnapshotBuilder(
        conversation_log_reader=InMemoryConversationReader(),
        embedding_backend=CountingEmbeddingBackend(),  # type: ignore[arg-type]
        memory_store=FakeMemoryStore(),  # type: ignore[arg-type]
        session_summary_store=summary_store,  # type: ignore[arg-type]
    )

    snapshot = await builder.build(
        text="著作権の話、詳しくはどんな話やったっけ",
        speaker=None,
        device_id="local",
        active_session_id=None,
        policy=ContextBuildPolicy.for_depth("deep"),
    )

    assert summary_store.read_calls == 1
    restored_texts = [
        hit.text
        for hit in snapshot.memory_hits
        if hit.source_id and hit.source_id.startswith("restored_turn:")
    ]
    assert restored_texts == [
        "著作権の話では、AIに学習させる入力側の許諾が気になっていた。",
        "生成物よりも、作る過程で誰の作品を使ったかを重く見ていた。",
    ]
    assert snapshot.trace.included_counts["restored_turn_snippets"] == 2
    assert snapshot.trace.cue_type == "detail"
    selected_sources = {
        trace.source
        for trace in snapshot.trace.source_score_traces
        if trace.selected
    }
    assert "user_turn_snippet" in selected_sources
    assert "tomoko_turn_snippet" not in selected_sources


@pytest.mark.unit
async def test_context_source_quota_keeps_tomoko_turns_from_dominating() -> None:
    summary_store = RestoringSummaryStore()
    builder = ContextSnapshotBuilder(
        conversation_log_reader=InMemoryConversationReader(),
        embedding_backend=FakeEmbeddingBackend(),  # type: ignore[arg-type]
        memory_store=FakeMemoryStore(),  # type: ignore[arg-type]
        session_summary_store=summary_store,  # type: ignore[arg-type]
    )

    snapshot = await builder.build(
        text="著作権の話、どういう風に考えてたっけ",
        speaker=None,
        device_id="local",
        active_session_id=None,
        policy=ContextBuildPolicy.for_depth("deep"),
    )

    restored_tomoko = [
        hit
        for hit in snapshot.memory_hits
        if hit.source_id
        and hit.source_id.startswith("restored_turn:")
        and hit.speaker == "tomoko"
    ]
    assert [hit.text for hit in restored_tomoko] == [
        "つまり、結論としては入力の扱いを分けて考えたいんだね。"
    ]
    assert all(
        not (
            trace.source == "tomoko_turn_snippet"
            and trace.selected
            and trace.final_score
            > max(
                user_trace.final_score
                for user_trace in snapshot.trace.source_score_traces
                if user_trace.source == "user_turn_snippet"
                and user_trace.selected
            )
        )
        for trace in snapshot.trace.source_score_traces
    )


@pytest.mark.unit
async def test_memory_cue_type_changes_source_weighting() -> None:
    detail_builder = ContextSnapshotBuilder(
        conversation_log_reader=InMemoryConversationReader(),
        embedding_backend=FakeEmbeddingBackend(),  # type: ignore[arg-type]
        memory_store=FakeMemoryStore(),  # type: ignore[arg-type]
        session_summary_store=RestoringSummaryStore(),  # type: ignore[arg-type]
    )
    stance_builder = ContextSnapshotBuilder(
        conversation_log_reader=InMemoryConversationReader(),
        embedding_backend=FakeEmbeddingBackend(),  # type: ignore[arg-type]
        memory_store=FakeMemoryStore(),  # type: ignore[arg-type]
        session_summary_store=RestoringSummaryStore(),  # type: ignore[arg-type]
    )

    detail_snapshot = await detail_builder.build(
        text="詳しくはどんな話やったっけ",
        speaker=None,
        device_id="local",
        active_session_id=None,
        policy=ContextBuildPolicy.for_depth("deep"),
    )
    stance_snapshot = await stance_builder.build(
        text="どういう風に考えてたっけ",
        speaker=None,
        device_id="local",
        active_session_id=None,
        policy=ContextBuildPolicy.for_depth("deep"),
    )

    detail_user_weight = next(
        trace.source_weight
        for trace in detail_snapshot.trace.source_score_traces
        if trace.source == "user_turn_snippet"
    )
    stance_user_weight = next(
        trace.source_weight
        for trace in stance_snapshot.trace.source_score_traces
        if trace.source == "user_turn_snippet"
    )
    assert detail_snapshot.trace.cue_type == "detail"
    assert stance_snapshot.trace.cue_type == "stance"
    assert detail_user_weight > stance_user_weight


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
    assert snapshot.trace.skipped_reasons == {
        "recent_turns": "timed_out",
        "same_session_turns": "timed_out",
    }
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


@pytest.mark.unit
async def test_tomoro_session_uses_larger_budget_for_explicit_memory_cue() -> None:
    transcript = Transcript(
        text="トモコ、この前話していたAIの話って覚えてる？",
        device_id="local",
        speaker=None,
        audio_level_db=-20.0,
        recorded_at=datetime(2026, 5, 24, 9, 1, tzinfo=UTC),
        is_final=True,
    )
    mode = RecordingThinkingMode()
    session = TomoroSession(
        vad_processor=FakeVADProcessor(),  # type: ignore[arg-type]
        send_event=lambda event: None,
        router=FakeRouter(),  # type: ignore[arg-type]
        thinking_mode=mode,
        context_snapshot_builder=ContextSnapshotBuilder(
            conversation_log_reader=InMemoryConversationReader()
        ),
    )

    await session._reply_to(transcript)
    await session._wait_for_reply_task()

    assert mode.inputs
    snapshot = mode.inputs[0].context_snapshot
    assert snapshot is not None
    assert snapshot.depth == "deep"
    assert snapshot.trace.budget_ms == 300


@pytest.mark.unit
async def test_tomoro_session_carries_deep_memory_into_short_followup() -> None:
    mode = RecordingThinkingMode()
    session = TomoroSession(
        vad_processor=FakeVADProcessor(),  # type: ignore[arg-type]
        send_event=lambda event: None,
        router=FakeRouter(),  # type: ignore[arg-type]
        thinking_mode=mode,
        context_snapshot_builder=ContextSnapshotBuilder(
            conversation_log_reader=InMemoryConversationReader(),
            embedding_backend=FakeEmbeddingBackend(),  # type: ignore[arg-type]
            memory_store=FakeMemoryStore(),  # type: ignore[arg-type]
            session_summary_store=FakeSummaryStore(),  # type: ignore[arg-type]
        ),
    )
    session.active_conversation_session_id = uuid4()

    await session._reply_to(
        Transcript(
            text="著作権の話とか覚えてる",
            device_id="local",
            speaker=None,
            audio_level_db=-20.0,
            recorded_at=datetime(2026, 5, 24, 9, 1, tzinfo=UTC),
            is_final=True,
        )
    )
    await session._wait_for_reply_task()

    await session._reply_to(
        Transcript(
            text="どういう風に考えてたっけ",
            device_id="local",
            speaker=None,
            audio_level_db=-20.0,
            recorded_at=datetime(2026, 5, 24, 9, 2, tzinfo=UTC),
            is_final=True,
        )
    )
    await session._wait_for_reply_task()

    assert len(mode.inputs) == 2
    followup_input = mode.inputs[1]
    assert followup_input.context_snapshot is not None
    assert followup_input.context_snapshot.depth == "fast"
    assert [hit.text for hit in followup_input.long_term_memory] == [
        "会話セッション要約: カレーの材料と買い物について話した。",
        "トモコ、この前のカレー覚えてる？",
        "スパイスはクミンの話をしていたよ。",
    ]


@pytest.mark.unit
async def test_tomoro_session_suppresses_self_statement_memory_prompt() -> None:
    mode = RecordingThinkingMode()
    session = TomoroSession(
        vad_processor=FakeVADProcessor(),  # type: ignore[arg-type]
        send_event=lambda event: None,
        router=FakeRouter(),  # type: ignore[arg-type]
        thinking_mode=mode,
        context_snapshot_builder=ContextSnapshotBuilder(
            conversation_log_reader=InMemoryConversationReader(),
            embedding_backend=FakeEmbeddingBackend(),  # type: ignore[arg-type]
            memory_store=FakeMemoryStore(),  # type: ignore[arg-type]
            session_summary_store=FakeSummaryStore(),  # type: ignore[arg-type]
        ),
    )
    session.active_conversation_session_id = uuid4()

    await session._reply_to(
        Transcript(
            text="普通に覚えております",
            device_id="local",
            speaker=None,
            audio_level_db=-20.0,
            recorded_at=datetime(2026, 5, 30, 10, 10, tzinfo=UTC),
            is_final=True,
        )
    )
    await session._wait_for_reply_task()

    assert len(mode.inputs) == 1
    first_input = mode.inputs[0]
    assert first_input.context_snapshot is not None
    assert first_input.context_snapshot.depth == "fast"
    assert first_input.long_term_memory == []
    assert session._carried_long_term_memory() == []


@pytest.mark.unit
async def test_tomoro_session_carries_calendar_context_into_short_followup() -> None:
    calendar_store = InMemoryCalendarEventStore()
    await calendar_store.replace_source_events(
        source_id="gcal",
        events=[_calendar_event("家族の予定")],
    )
    mode = RecordingThinkingMode()
    session = TomoroSession(
        vad_processor=FakeVADProcessor(),  # type: ignore[arg-type]
        send_event=lambda event: None,
        router=FakeRouter(),  # type: ignore[arg-type]
        thinking_mode=mode,
        context_snapshot_builder=ContextSnapshotBuilder(
            conversation_log_reader=InMemoryConversationReader(),
            calendar_store=calendar_store,
            now_provider=lambda: datetime(2026, 5, 30, 0, 0, tzinfo=UTC),
        ),
    )
    session.active_conversation_session_id = uuid4()

    await session._reply_to(
        Transcript(
            text="今日の予定ある？",
            device_id="local",
            speaker=None,
            audio_level_db=-20.0,
            recorded_at=datetime(2026, 5, 30, 0, 0, tzinfo=UTC),
            is_final=True,
        )
    )
    await session._wait_for_reply_task()

    await session._reply_to(
        Transcript(
            text="それ詳しく",
            device_id="local",
            speaker=None,
            audio_level_db=-20.0,
            recorded_at=datetime(2026, 5, 30, 0, 1, tzinfo=UTC),
            is_final=True,
        )
    )
    await session._wait_for_reply_task()

    assert len(mode.inputs) == 2
    first_input = mode.inputs[0]
    assert first_input.context_snapshot is not None
    assert first_input.context_snapshot.depth == "deep"
    assert [event.summary for event in first_input.context_snapshot.calendar_events] == [
        "家族の予定"
    ]
    assert first_input.long_term_memory == []

    followup_input = mode.inputs[1]
    assert followup_input.context_snapshot is not None
    assert followup_input.context_snapshot.depth == "fast"
    assert [hit.text for hit in followup_input.long_term_memory] == [
        "カレンダー予定: 2026-05-30 13:00-14:00: 家族の予定 @ Kitchen"
    ]


@pytest.mark.unit
async def test_tomoro_session_deduplicates_carryover_against_fresh_retrieval() -> None:
    mode = RecordingThinkingMode()
    session = TomoroSession(
        vad_processor=FakeVADProcessor(),  # type: ignore[arg-type]
        send_event=lambda event: None,
        router=FakeRouter(),  # type: ignore[arg-type]
        thinking_mode=mode,
        context_snapshot_builder=ContextSnapshotBuilder(
            conversation_log_reader=InMemoryConversationReader(),
            embedding_backend=FakeEmbeddingBackend(),  # type: ignore[arg-type]
            memory_store=FakeMemoryStore(),  # type: ignore[arg-type]
            session_summary_store=FakeSummaryStore(),  # type: ignore[arg-type]
        ),
    )
    session.active_conversation_session_id = uuid4()

    for text in (
        "トモコ、この前のカレー覚えてる？",
        "もう一回この前のカレー覚えてる？",
    ):
        await session._reply_to(
            Transcript(
                text=text,
                device_id="local",
                speaker=None,
                audio_level_db=-20.0,
                recorded_at=datetime(2026, 5, 24, 9, 1, tzinfo=UTC),
                is_final=True,
            )
        )
        await session._wait_for_reply_task()

    second_texts = [hit.text for hit in mode.inputs[1].long_term_memory]
    assert second_texts == list(dict.fromkeys(second_texts))


@pytest.mark.unit
def test_tomoro_session_evicts_old_carryover_entries_by_text_budget() -> None:
    session = TomoroSession(
        vad_processor=FakeVADProcessor(),  # type: ignore[arg-type]
        send_event=lambda event: None,
    )
    entries = [
        MemoryHit(
            speaker="user",
            text=f"{index}:" + "長い記憶" * 80,
            timestamp=datetime(2026, 5, 24, 9, index, tzinfo=UTC),
            similarity=0.5 + index / 100,
        )
        for index in range(8)
    ]

    session._remember_retrieved_context(entries)

    carried_texts = [hit.text for hit in session._carried_long_term_memory()]
    assert carried_texts
    assert entries[0].text not in carried_texts
    assert sum(len(text) for text in carried_texts) <= 900


@pytest.mark.unit
async def test_tomoro_session_clears_carryover_when_session_closes() -> None:
    session = TomoroSession(
        vad_processor=FakeVADProcessor(),  # type: ignore[arg-type]
        send_event=lambda event: None,
    )
    session.active_conversation_session_id = uuid4()
    session._remember_retrieved_context(
        [
            MemoryHit(
                speaker="tomoko",
                text="会話セッション要約: 著作権の話",
                timestamp=datetime(2026, 5, 24, 9, 1, tzinfo=UTC),
                similarity=0.9,
            )
        ]
    )

    await session._close_conversation_session(end_reason="attention_timeout")

    assert session._carried_long_term_memory() == []


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
