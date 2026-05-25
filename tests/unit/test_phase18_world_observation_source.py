from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from server.shared.candidate import ThinkerSourceContext
from server.shared.models import WorldObservationInterpretationRecord
from server.thinker.sources.world_observation import WorldObservationSource


class FakeWorldObservationStore:
    def __init__(self, records) -> None:
        self.records = tuple(records)

    async def fetch_candidate_interpretations(
        self,
        *,
        limit: int,
        min_confidence: float = 0.45,
        min_interest: float = 0.45,
    ):
        del min_confidence, min_interest
        return self.records[:limit]


@pytest.mark.unit
async def test_world_observation_source_keeps_trace_tags() -> None:
    now = datetime(2026, 5, 25, 9, 0, tzinfo=UTC)
    interpretation = WorldObservationInterpretationRecord(
        id=uuid4(),
        item_id=uuid4(),
        document_id=uuid4(),
        topic="ai",
        title="小型モデル",
        summary="端末内推論",
        source_hint="sample",
        freshness="fresh",
        confidence=0.9,
        persona_state_version_id=None,
        persona_lexicon_version_id=None,
        relevance_to_user=0.7,
        tomoko_interest=0.8,
        emotional_tone="curious",
        memory_value=0.6,
        speakability_hint="短くなら話題にできる",
        interpretation_text="ローカル推論の話は少し気になる。",
        reason_json={},
        created_at=now,
    )
    source = WorldObservationSource(
        store=FakeWorldObservationStore([interpretation]),
    )

    seeds = await source.collect(ThinkerSourceContext(observed_at=now))

    assert len(seeds) == 1
    assert seeds[0].source == f"world_observation:{interpretation.id}"
    assert f"world_observation_document:{interpretation.document_id}" in seeds[0].context_tags
    assert (
        seeds[0].metadata_json["world_observation"]["document_id"]
        == str(interpretation.document_id)
    )
    assert seeds[0].urgent is True
