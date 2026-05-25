from __future__ import annotations

from datetime import UTC, datetime

import psycopg
import pytest

from server.shared.config import NodeConfig
from server.shared.models import (
    WorldObservationInterpretation,
    WorldObservationNormalizedBatch,
    WorldObservationNormalizedItem,
    WorldObservationNormalizeTrace,
)
from server.world_observations.raw_markdown import parse_raw_markdown
from server.world_observations.store import PostgresWorldObservationStore


@pytest.mark.integration
async def test_world_observation_store_idempotent_import_and_trace() -> None:
    config = NodeConfig.load("config/central_realtime.toml")
    dsn = config.database.dsn

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            for ddl in [
                "docker/postgres/init/002_ambient_logs.sql",
                "docker/postgres/init/004_conversation_sessions.sql",
                "docker/postgres/init/005_persona_snapshots.sql",
                "docker/postgres/init/006_candidates.sql",
                "docker/postgres/init/007_diary.sql",
                "docker/postgres/init/013_world_observations.sql",
            ]:
                await cur.execute(open(ddl, encoding="utf-8").read())
            await cur.execute(
                """
                DELETE FROM world_observation_documents
                WHERE sha256_checksum = 'phase18-checksum'
                """
            )
        await conn.commit()

    store = PostgresWorldObservationStore(dsn)
    document = parse_raw_markdown(_raw_markdown(), path="phase18.md")
    first, inserted = await store.import_raw_document_once(
        document,
        checksum="phase18-checksum",
        imported_at=datetime(2099, 5, 25, 0, 0, tzinfo=UTC),
    )
    second, inserted_again = await store.import_raw_document_once(
        document,
        checksum="phase18-checksum",
    )

    assert inserted is True
    assert inserted_again is False
    assert second.id == first.id

    items = await store.save_normalized_batch(
        first.id,
        WorldObservationNormalizedBatch(
            items=(
                WorldObservationNormalizedItem(
                    topic="ai",
                    title="小型モデル",
                    summary="端末内推論",
                    source_hint="sample",
                    freshness="fresh",
                    confidence=0.9,
                    raw_excerpt="端末内推論",
                ),
            ),
            trace=WorldObservationNormalizeTrace(
                model="integration",
                elapsed_ms=1.0,
                attempts=1,
            ),
        ),
    )
    pending = await store.fetch_items_without_interpretation(limit=10)
    pending_ids = {item.id for item in pending}
    assert items[0].id in pending_ids

    interpretation = await store.save_interpretation(
        WorldObservationInterpretation(
            item_id=items[0].id,
            relevance_to_user=0.7,
            tomoko_interest=0.8,
            emotional_tone="curious",
            memory_value=0.6,
            speakability_hint="short_now",
            interpretation_text="ローカル推論の話は少し気になる。",
            tomoko_private_reaction="手元で動く話は、少し身を乗り出したくなる。",
            candidate_seed_text="ローカル推論の話、少しだけ気になるかも。",
            reason_json={
                "persona_basis": "ローカル推論への関心",
                "user_basis": "開発作業に近い",
                "speakability_basis": "短く話題にできる",
                "avoid_overclaim": "integration fixture",
            },
        )
    )
    candidates = await store.fetch_candidate_interpretations(limit=10)

    assert interpretation.item_id == items[0].id
    assert interpretation in candidates

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                DELETE FROM world_observation_documents
                WHERE id = %s
                """,
                (first.id,),
            )
        await conn.commit()


def _raw_markdown() -> str:
    return """\
---
schema_version: 1
kind: world_observation_batch
generated_by: integration
observed_at: 2099-05-25T09:00:00+00:00
language: ja
topics: [ai]
source_policy: public_web_summary_only
collection_prompt_version: daily_world_observation_v1
---
本文。
"""
