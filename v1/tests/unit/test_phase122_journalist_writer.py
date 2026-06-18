from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from server.journalist.input import (
    AmbientDigest,
    ConversationTurnMaterial,
    JournalistInputSnapshot,
    SessionSummaryMaterial,
)
from server.journalist.main import DiaryWriter
from server.shared.diary import InMemoryDiaryStore


class FakeInputBuilder:
    def __init__(self, snapshot: JournalistInputSnapshot) -> None:
        self.snapshot = snapshot

    async def build(self, diary_date: date) -> JournalistInputSnapshot:
        assert diary_date == self.snapshot.diary_date
        return self.snapshot


class FakeBackend:
    name = "fake_diary"
    privacy_allowed = True

    def __init__(self, chunks: tuple[str, ...]) -> None:
        self.chunks = chunks
        self.calls: list[tuple[str, list[dict[str, str]]]] = []

    async def chat_stream(self, system_prompt: str, messages: list[dict[str, str]]):
        self.calls.append((system_prompt, messages))
        for chunk in self.chunks:
            yield chunk


class FakeRouter:
    def __init__(self, backend: FakeBackend) -> None:
        self.backend = backend
        self.select_calls: list[tuple[str, str]] = []

    async def select(self, role: str, preference: str):
        self.select_calls.append((role, preference))
        return self.backend


def _snapshot() -> JournalistInputSnapshot:
    session_id = uuid4()
    started_at = datetime(2026, 5, 24, tzinfo=UTC)
    return JournalistInputSnapshot(
        diary_date=date(2026, 5, 24),
        started_at=started_at,
        ended_at=datetime(2026, 5, 25, tzinfo=UTC),
        session_summaries=(
            SessionSummaryMaterial(
                id=session_id,
                started_at=started_at,
                ended_at=started_at,
                summary_text="朝に予定の話をした。",
            ),
        ),
        conversation_turns=(
            ConversationTurnMaterial(
                id=uuid4(),
                conversation_session_id=session_id,
                role="user",
                text="今日は少し眠い。",
                emotion=None,
                status="completed",
                recorded_at=started_at,
            ),
        ),
        ambient_digest=AmbientDigest(total_count=3, excerpts=("静かな時間があった",)),
        dismissed_candidates=(),
    )


@pytest.mark.unit
async def test_diary_writer_generates_and_saves_entry() -> None:
    snapshot = _snapshot()
    backend = FakeBackend(("今日は", "静かな日だった。"))
    router = FakeRouter(backend)
    store = InMemoryDiaryStore()
    writer = DiaryWriter(
        input_builder=FakeInputBuilder(snapshot),
        diary_store=store,
        router=router,
    )

    result = await writer.write_for_date(snapshot.diary_date)

    assert result.error_count == 0
    assert result.entry is not None
    assert result.entry.body_text == "今日は静かな日だった。"
    assert result.entry.source_session_ids == snapshot.source_session_ids
    assert await store.fetch_recent_entries(limit=1) == [result.entry]
    assert router.select_calls == [("diary", "privacy")]
    assert "朝に予定の話をした。" in backend.calls[0][1][0]["content"]


@pytest.mark.unit
async def test_diary_writer_does_not_save_empty_output() -> None:
    snapshot = _snapshot()
    backend = FakeBackend(("  ", "\n"))
    store = InMemoryDiaryStore()
    writer = DiaryWriter(
        input_builder=FakeInputBuilder(snapshot),
        diary_store=store,
        router=FakeRouter(backend),
    )

    result = await writer.write_for_date(snapshot.diary_date)

    assert result.entry is None
    assert result.error_count == 1
    assert await store.fetch_recent_entries(limit=1) == []
