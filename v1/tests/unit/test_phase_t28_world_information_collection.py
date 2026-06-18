from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from server.shared.candidate import ThinkerSourceContext
from server.shared.models import WorldObservationInterpretationRecord
from server.thinker.sources.world_observation import WorldObservationSource
from server.thinker.world_information import (
    DEFAULT_WORLD_TOPIC_SEEDS,
    WorldInformationCollectionWorker,
    build_seeded_world_prompt_template,
)
from server.world_observations.operator_client import (
    WorldObservationOperatorRequest,
    WorldObservationOperatorResult,
)


class FakeWorldObservationClient:
    def __init__(self, result: WorldObservationOperatorResult) -> None:
        self.result = result
        self.requests: list[WorldObservationOperatorRequest] = []

    async def observe(
        self,
        request: WorldObservationOperatorRequest,
    ) -> WorldObservationOperatorResult:
        self.requests.append(request)
        return self.result


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
        return tuple(
            record
            for record in self.records
            if record.confidence >= min_confidence
            and max(record.tomoko_interest, record.relevance_to_user) >= min_interest
        )[:limit]


@pytest.mark.unit
def test_build_seeded_world_prompt_template_adds_deterministic_topics() -> None:
    prompt = build_seeded_world_prompt_template(
        "本文\n",
        topic_seeds=DEFAULT_WORLD_TOPIC_SEEDS,
    )

    assert "deterministic thinker2 topic seeds" in prompt
    assert "- ai:" in prompt
    assert "- local_inference:" in prompt
    assert "private page" in prompt


@pytest.mark.unit
async def test_world_information_collection_worker_saves_raw_artifact(
    tmp_path: Path,
) -> None:
    body = _valid_world_body()
    client = FakeWorldObservationClient(
        WorldObservationOperatorResult(
            status="completed",
            title="world_observation_2026-06-16",
            observed_at="2026-06-16T09:00:00+09:00",
            markdown_text=body,
        )
    )
    worker = WorldInformationCollectionWorker(
        client=client,  # type: ignore[arg-type]
        prompt_template="title: `world_observation_2026-05-25`\nobserved_at: old\n",
        output_dir=tmp_path,
    )

    result = await worker.collect_once(
        collection_date="2026-06-16",
        observed_at="2026-06-16T09:00:00+09:00",
    )

    assert result.ok is True
    assert result.output_path is not None
    assert result.output_path.exists()
    assert "world_observation_2026-06-16" in client.requests[0].prompt
    assert "deterministic thinker2 topic seeds" in client.requests[0].prompt


@pytest.mark.unit
async def test_world_observation_source_filters_stale_and_sensitive_records() -> None:
    now = datetime(2026, 6, 16, 9, 0, tzinfo=UTC)
    fresh = _record(
        freshness="fresh",
        confidence=0.9,
        speakability_hint="low_intrusion",
        created_at=now,
    )
    stale = _record(
        freshness="stale",
        confidence=0.9,
        speakability_hint="low_intrusion",
        created_at=now,
    )
    sensitive = _record(
        freshness="fresh",
        confidence=0.9,
        speakability_hint="sensitive_private",
        created_at=now,
    )
    low_confidence = _record(
        freshness="fresh",
        confidence=0.2,
        speakability_hint="low_intrusion",
        created_at=now,
    )
    source = WorldObservationSource(
        store=FakeWorldObservationStore([fresh, stale, sensitive, low_confidence]),
    )

    seeds = await source.collect(ThinkerSourceContext(observed_at=now))

    assert [seed.source for seed in seeds] == [f"world_observation:{fresh.id}"]


def _record(
    *,
    freshness: str,
    confidence: float,
    speakability_hint: str,
    created_at: datetime,
) -> WorldObservationInterpretationRecord:
    return WorldObservationInterpretationRecord(
        id=uuid4(),
        item_id=uuid4(),
        document_id=uuid4(),
        topic="ai",
        title="AI update",
        summary="summary",
        source_hint="unit",
        freshness=freshness,  # type: ignore[arg-type]
        confidence=confidence,
        persona_state_version_id=None,
        persona_lexicon_version_id=None,
        relevance_to_user=0.8,
        tomoko_interest=0.8,
        emotional_tone="curious",
        memory_value=0.4,
        speakability_hint=speakability_hint,
        interpretation_text="気になる話題。",
        tomoko_private_reaction="少し気になる。",
        candidate_seed_text="少し話してもよさそう。",
        reason_json={},
        created_at=created_at,
    )


def _valid_world_body() -> str:
    topics = (
        "news",
        "economy",
        "technology",
        "culture",
        "local_life",
        "ai",
        "local_inference",
    )
    sections = []
    for topic in topics:
        sections.append(
            f"## {topic}\n"
            "事実: 公開ソースで確認できる観測を記録する。"
            "これは十分な長さの本文にするための説明です。\n"
            "推測・含意: 影響は限定的かもしれないが、生活や開発の話題になる。\n"
            "source_hint: official blog / Reuters / GitHub\n"
        )
    return "# 外界観測レポート 2026-06-16\n\n" + "\n".join(sections) * 8
