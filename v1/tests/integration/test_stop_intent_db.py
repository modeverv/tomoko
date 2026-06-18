from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from server.gateway.stop_intent import (
    PostgresStopIntentStore,
    StopIntentSignal,
    build_stop_observation,
)
from server.shared.config import NodeConfig


@pytest.mark.integration
async def test_postgres_stop_intent_store_round_trip() -> None:
    config = NodeConfig.load("config/central_realtime.toml")
    ddl = Path("docker/postgres/init/012_stop_intent.sql").read_text()
    store = PostgresStopIntentStore(config.database.dsn)
    observation = build_stop_observation(
        transcript_text="その話はいったん置いといて",
        conversation_session_id=None,
        turn_id="integration-turn",
        rule_kind="stop_candidate",
        adopted_action="observer",
        playback_state_json={"playback_state": "speaking"},
        reply_state_json={"first_reply_text_emitted": False},
    )

    with psycopg.connect(config.database.dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
            cur.execute(
                """
                DELETE FROM stop_intent_shadow_signals
                WHERE observation_id = %s
                """,
                (observation.id,),
            )
            cur.execute(
                """
                DELETE FROM stop_intent_observations
                WHERE id = %s OR transcript_id = %s
                """,
                (observation.id, observation.transcript_id),
            )

    try:
        await store.insert_observation(observation)
        claimed = await store.claim_next_observation()

        assert claimed is not None
        assert claimed.id == observation.id
        assert claimed.status == "processing"
        assert claimed.attempts == 1

        await store.record_signal(
            StopIntentSignal(
                observation_id=observation.id,
                method="embedding",
                predicted_kind="soft_stop",
                confidence=0.91,
                latency_ms=3.2,
                model="integration",
            )
        )
        await store.mark_completed(observation.id)

        with psycopg.connect(config.database.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT status
                    FROM stop_intent_observations
                    WHERE id = %s
                    """,
                    (observation.id,),
                )
                assert cur.fetchone()[0] == "completed"
                cur.execute(
                    """
                    SELECT predicted_kind
                    FROM stop_intent_shadow_signals
                    WHERE observation_id = %s
                    """,
                    (observation.id,),
                )
                assert cur.fetchone()[0] == "soft_stop"
    finally:
        with psycopg.connect(config.database.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM stop_intent_shadow_signals
                    WHERE observation_id = %s
                    """,
                    (observation.id,),
                )
                cur.execute(
                    """
                    DELETE FROM stop_intent_observations
                    WHERE id = %s
                    """,
                    (observation.id,),
                )
