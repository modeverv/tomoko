from __future__ import annotations

from collections.abc import AsyncGenerator
from uuid import UUID

import psycopg
import pytest

from server.background.session_summarizer import SessionSummarizer
from server.shared.config import NodeConfig
from server.shared.inference.backends.base import InferenceBackend
from server.shared.memory import PostgresConversationSessionSummaryStore


class FakeSummaryBackend(InferenceBackend):
    name = "fake_summary_llm"
    privacy_allowed = True

    async def chat_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
    ) -> AsyncGenerator[str, None]:
        del system_prompt, messages
        yield "カレーの材料と買い物について話した。"


class FakeRouter:
    async def select(self, role: str, preference: str = "latency") -> InferenceBackend:
        assert role == "session_summary"
        assert preference == "privacy"
        return FakeSummaryBackend()


class FakeEmbeddingBackend:
    name = "fake_e5"
    model = "intfloat/multilingual-e5-small"
    dimensions = 384
    privacy_allowed = True

    async def embed_query(self, text: str) -> list[float]:
        del text
        return [1.0] + [0.0] * 383

    async def embed_passage(self, text: str) -> list[float]:
        del text
        return [1.0] + [0.0] * 383


@pytest.mark.integration
async def test_postgres_session_summarizer_completes_pending_session() -> None:
    config = NodeConfig.load("config/central_realtime.toml")
    dsn = config.database.dsn
    session_id: UUID | None = None
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO conversation_sessions (
                    device_id,
                    start_reason,
                    ended_at,
                    end_reason,
                    summary_status
                )
                VALUES ('integration-test', 'called', now(), 'attention_timeout', 'pending')
                RETURNING id
                """
            )
            row = await cur.fetchone()
            assert row is not None
            session_id = row[0]
            await cur.execute(
                """
                INSERT INTO conversation_logs (
                    device_id,
                    speaker,
                    role,
                    transcript,
                    participation_mode,
                    status,
                    conversation_session_id
                )
                VALUES
                    (
                        'integration-test',
                        NULL,
                        'user',
                        'カレーの材料を覚えておいて',
                        'called',
                        'completed',
                        %s
                    ),
                    (
                        'integration-test',
                        'tomoko',
                        'tomoko',
                        '玉ねぎとスパイスだね',
                        'invited',
                        'completed',
                        %s
                    )
                """,
                (session_id, session_id),
            )

    try:
        summarizer = SessionSummarizer(
            session_summary_store=PostgresConversationSessionSummaryStore(dsn),
            router=FakeRouter(),  # type: ignore[arg-type]
            embedding_backend=FakeEmbeddingBackend(),  # type: ignore[arg-type]
        )

        assert await summarizer.process_pending(limit=1) == 1

        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT summary_status,
                           summary_text,
                           summary_model,
                           summary_embedding_model
                    FROM conversation_sessions
                    WHERE id = %s
                    """,
                    (session_id,),
                )
                row = await cur.fetchone()
        assert row == (
            "completed",
            "カレーの材料と買い物について話した。",
            "fake_summary_llm",
            "intfloat/multilingual-e5-small",
        )
    finally:
        if session_id is not None:
            async with await psycopg.AsyncConnection.connect(dsn) as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        DELETE FROM conversation_logs
                        WHERE conversation_session_id = %s
                        """,
                        (session_id,),
                    )
                    await cur.execute(
                        "DELETE FROM conversation_sessions WHERE id = %s",
                        (session_id,),
                    )
