from __future__ import annotations

from datetime import UTC, datetime

import psycopg
import pytest

from server.gateway.context import ContextSnapshotBuilder
from server.shared.config import NodeConfig
from server.shared.models import ContextBuildPolicy
from server.shared.research_results import PostgresResearchResultStore


class FakeResearchEmbeddingBackend:
    name = "fake_bge_m3"
    model = "fake_bge_m3"
    dimensions = 1024
    privacy_allowed = True

    async def embed_query(self, text: str) -> list[float]:
        del text
        return [1.0, *([0.0] * 1023)]

    async def embed_passage(self, text: str) -> list[float]:
        del text
        return [1.0, *([0.0] * 1023)]


@pytest.mark.integration
async def test_postgres_research_result_store_feeds_deep_context() -> None:
    config = NodeConfig.load("config/central_realtime.toml")
    dsn = config.database.dsn
    ddl = "docker/postgres/init/015_research_results.sql"
    result_ids = (
        "integration-research-openai",
        "integration-research-anthropic",
    )

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(open(ddl, encoding="utf-8").read())
            await cur.execute(
                "DELETE FROM research_results WHERE id = ANY(%s)",
                (list(result_ids),),
            )
        await conn.commit()

    store = PostgresResearchResultStore(dsn)
    try:
        await store.insert(
            result_id=result_ids[0],
            query="今日のOpenAI関連ニュース",
            summary_text="OpenAIの最新発表についてのdeep context用メモ。",
            embedding=[1.0, *([0.0] * 1023)],
            embedding_model="fake_bge_m3",
            provider="perplexity",
            fetched_at=datetime(2026, 5, 31, 10, 0, tzinfo=UTC),
            short_answer="OpenAIのニュースを短くまとめました。",
            citation_urls=("https://example.com/openai",),
            raw_artifact_path="/tmp/openai.json",
        )
        await store.insert(
            result_id=result_ids[1],
            query="今日のAnthropic関連ニュース",
            summary_text="Anthropicの最新発表についてのdeep context用メモ。",
            embedding=[0.0, 1.0, *([0.0] * 1022)],
            embedding_model="fake_bge_m3",
            provider="perplexity",
            fetched_at=datetime(2026, 5, 31, 10, 5, tzinfo=UTC),
            short_answer="Anthropicのニュースを短くまとめました。",
            citation_urls=("https://example.com/anthropic",),
        )

        hits = await store.search_similar(
            embedding=[1.0, *([0.0] * 1023)],
            limit=2,
        )

        assert [hit.result_id for hit in hits] == list(result_ids)
        assert hits[0].summary_text == "OpenAIの最新発表についてのdeep context用メモ。"
        assert hits[0].citation_urls == ("https://example.com/openai",)
        assert hits[0].raw_artifact_path == "/tmp/openai.json"
        assert hits[0].similarity > 0.99

        snapshot = await ContextSnapshotBuilder(
            embedding_backend=FakeResearchEmbeddingBackend(),  # type: ignore[arg-type]
            research_result_store=store,
        ).build(
            text="OpenAIについて知ってることある？",
            speaker="human",
            device_id="integration-test",
            active_session_id=None,
            policy=ContextBuildPolicy.for_depth("deep"),
        )

        assert [hit.result_id for hit in snapshot.research_results] == list(result_ids)
        assert snapshot.trace.included_counts["research_results"] == 2
    finally:
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM research_results WHERE id = ANY(%s)",
                    (list(result_ids),),
                )
            await conn.commit()
