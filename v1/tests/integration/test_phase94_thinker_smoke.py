from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg
import pytest

from server.shared.candidate import CandidateSeed, EvaluatedUtterance, PostgresCandidateStore
from server.shared.config import NodeConfig
from server.thinker.arrival import ArrivalPrecomputer
from server.thinker.main import ThinkerLoopConfig, ThinkerProcess


class StaticSource:
    async def collect(self, context):
        return [
            CandidateSeed(
                seed_text="integration thinker smoke seed",
                source="integration",
                priority=0.42,
                urgent=False,
                expires_at=context.observed_at + timedelta(minutes=30),
                dedupe_key=f"integration:phase94:{context.observed_at.isoformat()}",
            )
        ]


class StaticEvaluator:
    async def evaluate(self, seed, context):
        del context
        return EvaluatedUtterance(
            should_keep=True,
            generated_text="少し休憩する？",
            priority=0.7,
            urgent=False,
            reason="integration smoke",
            context_tags=(*seed.context_tags, "evaluated_by:integration"),
        )


class StaticBackend:
    name = "integration"
    privacy_allowed = True

    async def chat_stream(self, system_prompt, messages):
        del system_prompt, messages
        yield '{"behavior": "wait_silent", "utterance_text": null, "reason": "smoke"}'


class StaticRouter:
    async def select(self, role: str, preference: str = "latency") -> StaticBackend:
        assert role == "candidate_gen"
        assert preference == "privacy"
        return StaticBackend()


@pytest.mark.integration
async def test_thinker_once_saves_candidate_and_arrival_to_postgres() -> None:
    config = NodeConfig.load("config/central_realtime.toml")
    dsn = config.database.dsn
    now = datetime(2026, 5, 24, 15, 30, tzinfo=UTC)
    ddl = "docker/postgres/init/006_candidates.sql"

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(open(ddl, encoding="utf-8").read())

    store = PostgresCandidateStore(dsn)
    saved_ids: list[UUID] = []
    arrival_ids: list[UUID] = []

    try:
        thinker = ThinkerProcess(
            store=store,
            sources=[StaticSource()],
            evaluator=StaticEvaluator(),
            arrival_precomputer=ArrivalPrecomputer(
                store=store,
                router=StaticRouter(),  # type: ignore[arg-type]
            ),
            config=ThinkerLoopConfig(device_id="integration"),
        )

        result = await thinker.run_once(now=now)

        assert result.candidate.generated_seed_count == 1
        assert result.candidate.inserted_seed_count == 1
        assert result.candidate.kept_candidate_count == 1
        assert result.arrival is not None
        assert result.arrival.behavior == "wait_silent"

        active = await store.fetch_active_utterance_candidates(now=now, limit=20)
        assert any(candidate.maturity == 1 for candidate in active)
        fresh = await store.fetch_latest_fresh_arrival_candidate(
            now=now,
            device_id="integration",
        )
        assert fresh is not None
        arrival_ids.append(fresh.id)
    finally:
        active = await store.fetch_active_utterance_candidates(now=now, limit=20)
        saved_ids.extend(
            candidate.id
            for candidate in active
            if "integration:phase94:" in " ".join(candidate.context_tags)
        )
        fresh = await store.fetch_latest_fresh_arrival_candidate(
            now=now,
            device_id="integration",
        )
        if fresh is not None:
            arrival_ids.append(fresh.id)
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                if saved_ids:
                    await cur.execute(
                        "DELETE FROM utterance_candidates WHERE id = ANY(%s)",
                        (saved_ids,),
                    )
                if arrival_ids:
                    await cur.execute(
                        "DELETE FROM arrival_candidates WHERE id = ANY(%s)",
                        (arrival_ids,),
                    )
