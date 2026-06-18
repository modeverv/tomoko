from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from server.background.session_summarizer import SessionSummarizer
from server.shared.inference.backends.base import InferenceBackend
from server.shared.memory import NullConversationSessionSummaryStore
from server.shared.models import ConversationTurn, SessionSummaryHit


class FakeSummaryBackend(InferenceBackend):
    name = "fake_summary_llm"
    privacy_allowed = True

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.system_prompt: str | None = None
        self.messages: list[dict[str, str]] | None = None

    async def chat_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
    ) -> AsyncGenerator[str, None]:
        self.system_prompt = system_prompt
        self.messages = messages
        if self.fail:
            raise RuntimeError("summary backend failed")
        yield "買い物リストとカレーの話。"
        yield "次回はスパイスを確認する。"


class FakeRouter:
    def __init__(self, backend: FakeSummaryBackend) -> None:
        self.backend = backend
        self.selections: list[tuple[str, str]] = []

    async def select(self, role: str, preference: str = "latency") -> InferenceBackend:
        self.selections.append((role, preference))
        return self.backend


class FakeEmbeddingBackend:
    name = "fake_bge_m3"
    model = "BAAI/bge-m3"
    dimensions = 1024
    privacy_allowed = True

    async def embed_query(self, text: str) -> list[float]:
        return [float(len(text)), 0.2, 0.3]

    async def embed_passage(self, text: str) -> list[float]:
        return [float(len(text)), 0.5, 0.6]


class InMemorySessionSummaryStore:
    def __init__(self) -> None:
        self.session_id = uuid4()
        self.pending: list[UUID] = [self.session_id]
        self.processing: list[UUID] = []
        self.completed: list[tuple[UUID, str, list[float], str, str]] = []
        self.errors: list[tuple[UUID, str]] = []
        self.turns: list[ConversationTurn] = [
            ConversationTurn(
                speaker="user",
                text="トモコ、カレーの材料を覚えておいて。",
                timestamp=datetime(2026, 5, 24, 10, 0, tzinfo=UTC),
            ),
            ConversationTurn(
                speaker="tomoko",
                text="うん、スパイスと玉ねぎだね。",
                timestamp=datetime(2026, 5, 24, 10, 1, tzinfo=UTC),
                emotion="happy",
            ),
        ]
        self.search_embedding: list[float] | None = None

    async def claim_pending_sessions(self, *, limit: int) -> list[UUID]:
        claimed = self.pending[:limit]
        self.pending = self.pending[limit:]
        self.processing.extend(claimed)
        return claimed

    async def read_session_turns(self, *, session_id: UUID) -> list[ConversationTurn]:
        assert session_id == self.session_id
        return self.turns

    async def complete_summary(
        self,
        *,
        session_id: UUID,
        summary_text: str,
        summary_model: str,
        embedding: list[float],
        embedding_model: str,
    ) -> None:
        self.completed.append(
            (session_id, summary_text, embedding, summary_model, embedding_model)
        )
        self.processing.remove(session_id)

    async def mark_summary_error(self, *, session_id: UUID, error: str) -> None:
        self.errors.append((session_id, error))
        self.processing.remove(session_id)

    async def search_similar_summaries(
        self,
        *,
        embedding: list[float],
        limit: int,
    ) -> list[SessionSummaryHit]:
        self.search_embedding = embedding
        assert limit == 3
        return [
            SessionSummaryHit(
                session_id=self.session_id,
                summary_text="カレーの材料の話をした。",
                started_at=datetime(2026, 5, 24, 10, 0, tzinfo=UTC),
                ended_at=datetime(2026, 5, 24, 10, 5, tzinfo=UTC),
                similarity=0.91,
            )
        ]


@pytest.mark.unit
async def test_session_summarizer_completes_pending_session() -> None:
    backend = FakeSummaryBackend()
    router = FakeRouter(backend)
    embedding_backend = FakeEmbeddingBackend()
    store = InMemorySessionSummaryStore()
    summarizer = SessionSummarizer(
        session_summary_store=store,  # type: ignore[arg-type]
        router=router,  # type: ignore[arg-type]
        embedding_backend=embedding_backend,  # type: ignore[arg-type]
    )

    processed = await summarizer.process_pending(limit=1)

    assert processed == 1
    assert router.selections == [("session_summary", "privacy")]
    assert backend.messages == [
        {
            "role": "user",
            "content": (
                "ユーザー: トモコ、カレーの材料を覚えておいて。\n"
                "トモコ: うん、スパイスと玉ねぎだね。"
            ),
        }
    ]
    assert store.completed == [
        (
            store.session_id,
            "買い物リストとカレーの話。次回はスパイスを確認する。",
            [26.0, 0.5, 0.6],
            "fake_summary_llm",
            "BAAI/bge-m3",
        )
    ]


@pytest.mark.unit
async def test_session_summarizer_marks_error_without_losing_source_logs() -> None:
    router = FakeRouter(FakeSummaryBackend(fail=True))
    store = InMemorySessionSummaryStore()
    summarizer = SessionSummarizer(
        session_summary_store=store,  # type: ignore[arg-type]
        router=router,  # type: ignore[arg-type]
        embedding_backend=FakeEmbeddingBackend(),  # type: ignore[arg-type]
    )

    processed = await summarizer.process_pending(limit=1)

    assert processed == 1
    assert store.completed == []
    assert store.errors == [(store.session_id, "summary backend failed")]
    assert store.turns[0].text == "トモコ、カレーの材料を覚えておいて。"


@pytest.mark.unit
async def test_session_summary_search_returns_related_session() -> None:
    store = InMemorySessionSummaryStore()
    hits = await store.search_similar_summaries(embedding=[0.1, 0.2], limit=3)

    assert hits[0].summary_text == "カレーの材料の話をした。"
    assert hits[0].similarity == 0.91


@pytest.mark.unit
async def test_null_session_summary_store_keeps_background_boundary_noop() -> None:
    store = NullConversationSessionSummaryStore()

    assert await store.claim_pending_sessions(limit=10) == []
    assert await store.read_session_turns(session_id=uuid4()) == []
    await store.complete_summary(
        session_id=uuid4(),
        summary_text="要約",
        summary_model="fake",
        embedding=[0.1],
        embedding_model="fake_e5",
    )
    await store.mark_summary_error(session_id=uuid4(), error="failed")
    assert await store.search_similar_summaries(embedding=[0.1], limit=3) == []
