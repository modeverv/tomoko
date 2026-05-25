from __future__ import annotations

from pathlib import Path

import pytest

from server.shared.models import (
    WorldObservationNormalizedBatch,
    WorldObservationNormalizedItem,
    WorldObservationNormalizeTrace,
)
from server.world_observations.ingest import WorldObservationIngestor
from server.world_observations.store import InMemoryWorldObservationStore


class FakeNormalizer:
    async def normalize(self, document) -> WorldObservationNormalizedBatch:
        del document
        return WorldObservationNormalizedBatch(
            items=(
                WorldObservationNormalizedItem(
                    topic="ai",
                    title="小型モデル",
                    summary="端末内推論の話題",
                    source_hint="sample",
                    freshness="fresh",
                    confidence=0.8,
                    raw_excerpt="端末内推論",
                ),
            ),
            trace=WorldObservationNormalizeTrace(
                model="fake",
                elapsed_ms=1.0,
                attempts=1,
            ),
        )


@pytest.mark.unit
async def test_ingest_archives_valid_artifact(tmp_path: Path) -> None:
    work = tmp_path / "work"
    archive = tmp_path / "archived"
    failed = tmp_path / "failed"
    work.mkdir()
    artifact = work / "sample.md"
    artifact.write_text(_valid_markdown())
    ingestor = WorldObservationIngestor(
        store=InMemoryWorldObservationStore(),
        normalizer=FakeNormalizer(),
        archive_root=archive,
        failed_root=failed,
    )

    result = await ingestor.ingest_path(artifact)

    assert result.action == "archived"
    assert result.archived_path is not None
    assert result.archived_path.exists()
    assert not artifact.exists()


@pytest.mark.unit
async def test_ingest_moves_invalid_artifact_to_failed_with_sidecar(tmp_path: Path) -> None:
    work = tmp_path / "work"
    archive = tmp_path / "archived"
    failed = tmp_path / "failed"
    work.mkdir()
    artifact = work / "bad.md"
    artifact.write_text("---\nschema_version: 1\n---\nbody")
    ingestor = WorldObservationIngestor(
        store=InMemoryWorldObservationStore(),
        normalizer=FakeNormalizer(),
        archive_root=archive,
        failed_root=failed,
    )

    result = await ingestor.ingest_path(artifact)

    assert result.action == "failed"
    assert result.failed_path is not None
    assert result.failed_path.exists()
    assert result.failed_path.with_name(f"{result.failed_path.name}.error.json").exists()


def _valid_markdown() -> str:
    return """\
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
