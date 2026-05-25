from __future__ import annotations

import pytest

from server.world_observations.normalizer import (
    WorldObservationNormalizer,
    parse_normalizer_output,
)
from server.world_observations.raw_markdown import parse_raw_markdown

pytestmark = pytest.mark.unit


class FakeBackend:
    name = "fake-normalizer"

    def __init__(self, text: str) -> None:
        self.text = text

    async def chat_stream(self, system_prompt: str, messages: list[dict[str, str]]):
        assert "background normalizer" in system_prompt
        assert messages
        yield self.text


def test_parse_normalizer_output_rejects_missing_required_field() -> None:
    items, issues = parse_normalizer_output(
        '{"items":[{"topic":"ai","title":"","summary":"s","source_hint":"x",'
        '"freshness":"fresh","confidence":0.8,"raw_excerpt":"e"}]}'
    )

    assert items == []
    assert issues[0].field == "items[0].title"


@pytest.mark.unit
async def test_normalizer_returns_trace_and_low_confidence_warning() -> None:
    document = parse_raw_markdown(
        """\
---
schema_version: 1
kind: world_observation_batch
generated_by: sample
observed_at: 2026-05-25T09:00:00+09:00
language: ja
topics: [ai]
source_policy: public_web_summary_only
collection_prompt_version: daily_world_observation_v1
---
本文。
"""
    )
    normalizer = WorldObservationNormalizer(
        backend=FakeBackend(
            '{"items":[{"topic":"ai","title":"小型モデル","summary":"端末内推論",'
            '"source_hint":"sample","freshness":"fresh","confidence":0.3,'
            '"raw_excerpt":"端末内推論"}]}'
        )
    )

    batch = await normalizer.normalize(document)

    assert len(batch.items) == 1
    assert batch.trace.model == "fake-normalizer"
    assert any(issue.severity == "warning" for issue in batch.trace.issues)
