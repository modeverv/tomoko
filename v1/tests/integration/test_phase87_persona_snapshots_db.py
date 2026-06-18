from __future__ import annotations

from uuid import UUID

import psycopg
import pytest

from server.shared.config import NodeConfig
from server.shared.models import (
    PersonaLexiconSnapshot,
    PersonaStateSnapshot,
    PersonaVersionDiff,
)
from server.shared.persona import PostgresPersonaSnapshotStore


@pytest.mark.integration
async def test_postgres_persona_snapshots_round_trip_and_jsonb_query() -> None:
    config = NodeConfig.load("config/central_realtime.toml")
    dsn = config.database.dsn
    store = PostgresPersonaSnapshotStore(dsn)
    session_id: UUID | None = None
    lexicon_version_id: UUID | None = None
    state_version_id: UUID | None = None

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO conversation_sessions (
                    device_id,
                    start_reason,
                    ended_at,
                    end_reason,
                    summary_status,
                    summary_text
                )
                VALUES (
                    'integration-test',
                    'called',
                    now(),
                    'attention_timeout',
                    'completed',
                    'カレーの材料と買い物について話した。'
                )
                RETURNING id
                """
            )
            row = await cur.fetchone()
            assert row is not None
            session_id = row[0]

    try:
        lexicon_version_id = await store.write_lexicon_version(
            source_session_id=session_id,
            reason="session_summary_completed",
            snapshot=PersonaLexiconSnapshot.from_json(
                {
                    "schema_version": 1,
                    "user_terms": [
                        {
                            "term": "カレーの話",
                            "meaning": "材料と買い物の話題",
                            "salience": 0.8,
                            "evidence": ["カレーの材料と買い物について話した。"],
                        }
                    ],
                }
            ),
            diff=PersonaVersionDiff.from_json(
                {
                    "schema_version": 1,
                    "added": [
                        {
                            "path": "$.user_terms",
                            "value": {"term": "カレーの話"},
                            "reason": "session summary に残った",
                        }
                    ],
                }
            ),
            model="fake_persona_extractor",
        )
        state_version_id = await store.write_state_version(
            source_session_id=session_id,
            reason="session_summary_completed",
            snapshot=PersonaStateSnapshot.from_json(
                {
                    "schema_version": 1,
                    "traits": {"warmth": 0.73},
                    "relationship": {"familiarity": 0.63},
                    "speaking_style": {
                        "sentence_length": "short",
                        "signature_phrases": ["うん"],
                    },
                }
            ),
            diff=PersonaVersionDiff.from_json(
                {
                    "schema_version": 1,
                    "updated": [
                        {
                            "path": "$.relationship.familiarity",
                            "from": 0.61,
                            "to": 0.63,
                            "reason": "会話が自然に継続した",
                        }
                    ],
                }
            ),
            model="fake_persona_extractor",
        )

        latest_lexicon = await store.read_latest_lexicon()
        latest_state = await store.read_latest_state()

        assert latest_lexicon is not None
        assert latest_lexicon.user_terms[0].term == "カレーの話"
        assert latest_state is not None
        assert latest_state.relationship.familiarity == 0.63

        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id
                    FROM persona_lexicon_versions
                    WHERE lexicon_json @> %s::jsonb
                    """,
                    ('{"user_terms": [{"term": "カレーの話"}]}',),
                )
                rows = await cur.fetchall()
        assert lexicon_version_id in {row[0] for row in rows}
    finally:
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                if lexicon_version_id is not None:
                    await cur.execute(
                        "DELETE FROM persona_lexicon_versions WHERE id = %s",
                        (lexicon_version_id,),
                    )
                if state_version_id is not None:
                    await cur.execute(
                        "DELETE FROM persona_state_versions WHERE id = %s",
                        (state_version_id,),
                    )
                if session_id is not None:
                    await cur.execute(
                        "DELETE FROM conversation_sessions WHERE id = %s",
                        (session_id,),
                    )
