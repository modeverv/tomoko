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

_CHECKSUM = "phase18-checksum"


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
                "DELETE FROM world_observation_documents WHERE sha256_checksum = %s",
                (_CHECKSUM,),
            )
        await conn.commit()

    store = PostgresWorldObservationStore(dsn)
    document = parse_raw_markdown(_raw_markdown(), path="phase18.md")
    try:
        first, inserted = await store.import_raw_document_once(
            document,
            checksum=_CHECKSUM,
            imported_at=datetime(2099, 5, 25, 0, 0, tzinfo=UTC),
        )
        second, inserted_again = await store.import_raw_document_once(
            document,
            checksum=_CHECKSUM,
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
        assert await _item_is_pending_without_interpretation(dsn, item_id=items[0].id)

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

        trace = await _fetch_fixture_trace(dsn, interpretation_id=interpretation.id)
        assert interpretation.item_id == items[0].id
        assert trace == {
            "document_id": first.id,
            "item_id": items[0].id,
            "interpretation_id": interpretation.id,
            "sha256_checksum": _CHECKSUM,
            "candidate_seed_text": "ローカル推論の話、少しだけ気になるかも。",
        }
    finally:
        await _delete_fixture_document(dsn)


async def _item_is_pending_without_interpretation(dsn: str, *, item_id: object) -> bool:
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT p.id IS NULL
                FROM world_observation_items i
                LEFT JOIN world_observation_interpretations p
                  ON p.item_id = i.id
                WHERE i.id = %s
                """,
                (item_id,),
            )
            row = await cur.fetchone()
    return bool(row and row[0])


async def _fetch_fixture_trace(
    dsn: str,
    *,
    interpretation_id: object,
) -> dict[str, object]:
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT
                    document_id,
                    item_id,
                    interpretation_id,
                    sha256_checksum,
                    candidate_seed_text
                FROM world_observation_trace
                WHERE interpretation_id = %s
                """,
                (interpretation_id,),
            )
            row = await cur.fetchone()
    if row is None:
        raise AssertionError("fixture interpretation was not visible in trace view")
    return {
        "document_id": row[0],
        "item_id": row[1],
        "interpretation_id": row[2],
        "sha256_checksum": row[3],
        "candidate_seed_text": row[4],
    }


async def _delete_fixture_document(dsn: str) -> None:
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM world_observation_documents WHERE sha256_checksum = %s",
                (_CHECKSUM,),
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
