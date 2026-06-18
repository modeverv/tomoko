from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

from server.gateway.initiative_feedback import (
    CandidateFeedbackScope,
    CandidateFeedbackSignal,
    PostgresCandidateFeedbackStore,
)
from server.shared.config import NodeConfig


@pytest.mark.integration
async def test_postgres_candidate_feedback_store_round_trip() -> None:
    config = NodeConfig.load("config/central_realtime.toml")
    ddl = Path("docker/postgres/init/011_initiative_feedback.sql").read_text()
    store = PostgresCandidateFeedbackStore(config.database.dsn)
    observed_at = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    scope = CandidateFeedbackScope(
        source="integration-phase106",
        topic="laundry",
        emotional_need="high",
    )

    with psycopg.connect(config.database.dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
            cur.execute(
                """
                DELETE FROM initiative_feedback_signals
                WHERE source = 'integration-phase106'
                   OR topic = 'laundry'
                """
            )

    try:
        await store.record(
            CandidateFeedbackSignal(
                scope=scope,
                kind="rejection",
                score=1.0,
                observed_at=observed_at,
                transcript_text="それ今じゃない",
            )
        )

        summary = await store.summarize(scope, now=observed_at)

        assert summary.rejection_score == 1.0
        assert summary.feedback_penalty > 0.0
        assert summary.acceptance_score == 0.0
    finally:
        with psycopg.connect(config.database.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM initiative_feedback_signals
                    WHERE source = 'integration-phase106'
                       OR topic = 'laundry'
                    """
                )
