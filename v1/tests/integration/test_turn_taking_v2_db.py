from __future__ import annotations

import asyncio
from uuid import uuid4

import psycopg
import pytest

from server.background.turn_taking_v2_worker import TurnTakingV2Worker
from server.shared.config import NodeConfig
from server.shared.turn_taking_v2 import PostgresTurnTakingV2Store


@pytest.mark.integration
async def test_turn_taking_v2_integration_flow() -> None:
    config = NodeConfig.load("config/central_realtime.toml")
    dsn = config.database.dsn

    ddl = "docker/postgres/init/018_turn_taking_v2.sql"
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(open(ddl, encoding="utf-8").read())
        await conn.commit()

    store = PostgresTurnTakingV2Store(dsn)
    session_id = uuid4()
    turn_id = uuid4()

    # Create dummy session to avoid Foreign Key constraint violation
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO conversation_sessions (id, device_id, start_reason) VALUES (%s, %s, %s)",
                (session_id, "test-device", "wake_word"),
            )
        await conn.commit()

    try:
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as notify_conn:
            await notify_conn.execute("LISTEN turn_taking_v2_observation")

            obs_id_task = store.save_observation(
                conversation_session_id=session_id,
                turn_id=turn_id,
                revision=1,
                vad_state="listening",
                attention_mode="engaged",
                raw_text="インテグレーションテストの入力です",
                filtered_text="インテグレーションテストの入力です",
                stable_text=None,
                unstable_tail=None,
                audio_level_db=-18.5,
                source="integration_test",
            )

            obs_id = await obs_id_task

            async def wait_for_notify():
                async for notify in notify_conn.notifies():
                    return notify

            try:
                notify = await asyncio.wait_for(wait_for_notify(), timeout=2.0)
                assert notify is not None
                assert notify.payload == str(obs_id)
            except asyncio.TimeoutError:
                pytest.fail("Timeout waiting for turn_taking_v2_observation NOTIFY")

        worker = TurnTakingV2Worker(dsn)
        worker_task = asyncio.create_task(worker.run(recovery_interval_sec=1.0))

        try:
            obs_id2 = await store.save_observation(
                conversation_session_id=session_id,
                turn_id=turn_id,
                revision=2,
                vad_state="listening",
                attention_mode="engaged",
                raw_text="二回目の発話テキストですが、どうですか？",
                filtered_text="二回目の発話テキストですが、どうですか？",
                stable_text=None,
                unstable_tail=None,
                audio_level_db=-15.0,
                source="integration_test",
            )

            advisory_row = None
            for _ in range(50):
                async with await psycopg.AsyncConnection.connect(dsn) as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "SELECT id, proposal, reason FROM turn_taking_v2_advisories WHERE observation_id = %s",
                            (obs_id2,),
                        )
                        advisory_row = await cur.fetchone()
                if advisory_row is not None:
                    break
                await asyncio.sleep(0.1)

            assert advisory_row is not None, "Worker failed to generate advisory for observation"
            assert advisory_row[1] == "prepare_only"
            assert "valid_speech" in advisory_row[2]

            # Verify stable prefix split was updated in the DB
            async with await psycopg.AsyncConnection.connect(dsn) as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT stable_text, unstable_tail FROM partial_transcript_observations WHERE id = %s",
                        (obs_id2,),
                    )
                    obs_row = await cur.fetchone()

            assert obs_row is not None
            assert obs_row[0] == ""
            assert obs_row[1] == "二回目の発話テキストですが、どうですか？"

        finally:
            worker._stop_event.set()
            try:
                await asyncio.wait_for(worker_task, timeout=2.0)
            except asyncio.TimeoutError:
                pass
    finally:
        # DB cleanup
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM turn_taking_v2_advisories WHERE conversation_session_id = %s", (session_id,))
                await cur.execute("DELETE FROM partial_transcript_observations WHERE conversation_session_id = %s", (session_id,))
                await cur.execute("DELETE FROM conversation_sessions WHERE id = %s", (session_id,))
            await conn.commit()
