from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from server.journalist.input import (
    AmbientDigest,
    ConversationTurnMaterial,
    DismissedCandidateMaterial,
    JournalistInputBuilder,
    SessionSummaryMaterial,
)


class FakeJournalistReader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, datetime, datetime, int]] = []
        self.session_id = uuid4()
        self.candidate_id = uuid4()

    async def read_session_summaries(
        self,
        *,
        started_at: datetime,
        ended_at: datetime,
        limit: int,
    ) -> tuple[SessionSummaryMaterial, ...]:
        self.calls.append(("sessions", started_at, ended_at, limit))
        return (
            SessionSummaryMaterial(
                id=self.session_id,
                started_at=started_at,
                ended_at=ended_at,
                summary_text="朝に予定の話をした。",
            ),
        )

    async def read_conversation_turns(
        self,
        *,
        started_at: datetime,
        ended_at: datetime,
        limit: int,
    ) -> tuple[ConversationTurnMaterial, ...]:
        self.calls.append(("turns", started_at, ended_at, limit))
        return (
            ConversationTurnMaterial(
                id=uuid4(),
                conversation_session_id=self.session_id,
                role="tomoko",
                text="言いかけて止まった。",
                emotion="thinking",
                status="interrupted",
                recorded_at=started_at,
            ),
        )

    async def read_ambient_digest(
        self,
        *,
        started_at: datetime,
        ended_at: datetime,
        excerpt_limit: int,
    ) -> AmbientDigest:
        self.calls.append(("ambient", started_at, ended_at, excerpt_limit))
        return AmbientDigest(total_count=12, excerpts=("部屋が静かだった",))

    async def read_dismissed_candidates(
        self,
        *,
        started_at: datetime,
        ended_at: datetime,
        limit: int,
    ) -> tuple[DismissedCandidateMaterial, ...]:
        self.calls.append(("candidates", started_at, ended_at, limit))
        return (
            DismissedCandidateMaterial(
                id=self.candidate_id,
                seed="休憩の声かけ",
                generated_text="少し休まない？",
                priority=0.8,
                dismissed_at=ended_at,
            ),
        )


@pytest.mark.unit
async def test_journalist_input_builder_uses_day_bounds_and_dtos() -> None:
    reader = FakeJournalistReader()
    builder = JournalistInputBuilder(reader=reader, session_limit=3, turn_limit=5)

    snapshot = await builder.build(date(2026, 5, 24))

    assert snapshot.started_at == datetime(2026, 5, 24, tzinfo=UTC)
    assert snapshot.ended_at == datetime(2026, 5, 25, tzinfo=UTC)
    assert snapshot.session_summaries[0].summary_text == "朝に予定の話をした。"
    assert snapshot.conversation_turns[0].status == "interrupted"
    assert snapshot.ambient_digest.total_count == 12
    assert snapshot.dismissed_candidates[0].generated_text == "少し休まない？"
    assert snapshot.source_session_ids == (reader.session_id,)
    assert snapshot.source_candidate_ids == (reader.candidate_id,)
    assert ("sessions", snapshot.started_at, snapshot.ended_at, 3) in reader.calls
    assert ("turns", snapshot.started_at, snapshot.ended_at, 5) in reader.calls


@pytest.mark.unit
def test_conversation_turn_material_rejects_unknown_status() -> None:
    with pytest.raises(ValueError, match="status"):
        ConversationTurnMaterial(
            id=uuid4(),
            conversation_session_id=None,
            role="user",
            text="hello",
            emotion=None,
            status="draft",
            recorded_at=datetime(2026, 5, 24, tzinfo=UTC),
        )
