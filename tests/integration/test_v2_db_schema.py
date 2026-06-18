from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest

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
