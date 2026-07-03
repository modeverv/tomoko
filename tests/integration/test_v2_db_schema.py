from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from server.info.main import materialize_info_fixtures_from_payloads
from server.summary.main import materialize_summaries_from_db
from server.think.main import materialize_candidates_from_db
from server.tomoko.db_bridge import load_active_candidates

pytestmark = pytest.mark.integration


def test_v2_schema_can_insert_core_rows_when_database_is_available() -> None:
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is required for v2 DB integration test")
    ddl = Path("docker/postgres/init/100_v2_core.sql").read_text(encoding="utf-8")
    with psycopg.connect(dsn) as conn:
        conn.execute(ddl)
        session_id = conn.execute(
            "INSERT INTO v2_conversation_sessions DEFAULT VALUES RETURNING id"
        ).fetchone()[0]
        observation_id = conn.execute(
            """
            INSERT INTO v2_stt_observations (event_kind, text, is_final)
            VALUES ('final', 'hello', true)
            RETURNING id
            """
        ).fetchone()[0]
        utterance_id = conn.execute(
            """
            INSERT INTO v2_utterances (session_id, stt_observation_id, speaker, text)
            VALUES (%s, %s, 'user', 'hello')
            RETURNING id
            """,
            (session_id, observation_id),
        ).fetchone()[0]
        assert utterance_id is not None
        conn.execute("SELECT v2_notify_id('v2_stt_observation', %s)", (observation_id,))
        decision_id = conn.execute(
            """
            INSERT INTO v2_speech_scheduler_decisions (
                action,
                text_intent,
                llm_prompt_basis,
                reason,
                score,
                score_breakdown
            )
            VALUES (
                'replace_current',
                'reply',
                'user_reply: hello',
                'reply pressure crossed threshold',
                0.8,
                '{"reply": 0.8}'::jsonb
            )
            RETURNING id
            """
        ).fetchone()[0]
        order_id = conn.execute(
            """
            INSERT INTO v2_speech_orders (
                scheduler_decision_id,
                text,
                mode,
                reason,
                priority
            )
            VALUES (%s, 'hi', 'replace_current', 'reply', 80)
            RETURNING id
            """,
            (decision_id,),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO v2_semantic_saturation_observations (
                stt_observation_id,
                saturation,
                source,
                basis_text
            )
            VALUES (%s, 0.7, 'deterministic', 'hello')
            """,
            (observation_id,),
        )
        assert order_id is not None
        conn.execute("SELECT v2_notify_id('v2_speech_order', %s)", (order_id,))
        summary_id = conn.execute(
            """
            INSERT INTO v2_session_summaries (
                session_id,
                keyword,
                conclusion,
                summary_text
            )
            VALUES (%s, 'hello', 'greeted briefly', 'hello: greeted briefly')
            RETURNING id
            """,
            (session_id,),
        ).fetchone()[0]
        embedding_id = conn.execute(
            """
            INSERT INTO v2_summary_embeddings (summary_id, embedding)
            VALUES (%s, ARRAY[0.1, 0.2, 0.3]::double precision[])
            RETURNING id
            """,
            (summary_id,),
        ).fetchone()[0]
        assert embedding_id is not None
        candidate_id = conn.execute(
            """
            INSERT INTO v2_candidates (
                seed_id,
                source,
                source_key,
                text,
                priority,
                urgency,
                intrusion,
                maturity,
                candidate_score,
                lifecycle,
                context_tags
            )
            VALUES (
                gen_random_uuid(),
                'world',
                'rain-now',
                'いま外は雨が降っている',
                0.8,
                0.6,
                0.1,
                1.0,
                0.9,
                'active',
                ARRAY['weather', 'world']::text[]
            )
            ON CONFLICT (source, source_key) DO UPDATE SET
                candidate_score = EXCLUDED.candidate_score
            RETURNING id
            """
        ).fetchone()[0]
        assert candidate_id is not None


@pytest.mark.asyncio
async def test_v2_materializers_chain_db_rows_into_active_candidates() -> None:
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is required for v2 DB integration test")
    ddl = Path("docker/postgres/init/100_v2_core.sql").read_text(encoding="utf-8")
    source_key = f"weather-rain-{uuid4()}"
    async with await psycopg.AsyncConnection.connect(
        dsn,
        autocommit=True,
        row_factory=dict_row,
    ) as conn:
        await conn.execute(ddl)
        session_cursor = await conn.execute(
            """
            INSERT INTO v2_conversation_sessions (ended_at, last_activity_at)
            VALUES (now(), now())
            RETURNING id
            """
        )
        session_row = await session_cursor.fetchone()
        assert session_row is not None
        session_id = session_row["id"]
        await conn.execute(
            """
            INSERT INTO v2_utterances (session_id, speaker, text)
            VALUES (%s, 'user', '予定 今日は会議が多い')
            """,
            (session_id,),
        )
        await conn.execute(
            """
            INSERT INTO v2_utterances (session_id, speaker, text)
            VALUES (%s, 'tomoko', '優先順位を整理しよう')
            """,
            (session_id,),
        )

        summary_result = await materialize_summaries_from_db(conn, limit=4)
        info_result = await materialize_info_fixtures_from_payloads(
            conn,
            calendar_items={"20260704T120000": "Design review"},
            world_items=[
                {
                    "source_key": source_key,
                    "text": "外は雨が強くなっている",
                    "confidence": 0.9,
                    "flags": {"stale": False, "sensitive": False},
                }
            ],
        )
        candidate_result = await materialize_candidates_from_db(
            conn,
            summary_limit=8,
            world_limit=8,
        )
        candidates = await load_active_candidates(conn, limit=1000)

    assert summary_result.sessions_read >= 1
    assert summary_result.summaries_upserted >= 1
    assert info_result.documents_upserted == 2
    assert info_result.interpretations_upserted == 2
    assert candidate_result.candidates_upserted >= 2
    assert any(candidate.source == "summary" for candidate in candidates)
    assert any("雨" in candidate.text for candidate in candidates)
