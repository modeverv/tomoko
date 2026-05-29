from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from server.session_memory_helpers import session_summary_hit_to_memory
from server.shared.models import SessionSummaryHit


def _summary_hit(*, ended_at: datetime | None) -> SessionSummaryHit:
    return SessionSummaryHit(
        session_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        summary_text="著作権の話をした",
        started_at=datetime(2026, 5, 29, 9, 0, tzinfo=UTC),
        ended_at=ended_at,
        similarity=0.8125,
    )


@pytest.mark.unit
def test_session_summary_hit_to_memory_preserves_prompt_payload_shape() -> None:
    ended_at = datetime(2026, 5, 29, 9, 30, tzinfo=UTC)

    memory = session_summary_hit_to_memory(_summary_hit(ended_at=ended_at))

    assert memory.speaker == "tomoko"
    assert memory.text == "会話セッション要約: 著作権の話をした"
    assert memory.timestamp == ended_at
    assert memory.similarity == 0.8125
    assert memory.emotion is None
    assert memory.source_id == "session_summary:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


@pytest.mark.unit
def test_session_summary_hit_to_memory_uses_started_at_when_ended_at_is_missing() -> None:
    hit = _summary_hit(ended_at=None)

    memory = session_summary_hit_to_memory(hit)

    assert memory.timestamp == hit.started_at
    assert memory.text == "会話セッション要約: 著作権の話をした"
    assert memory.source_id == "session_summary:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
