from __future__ import annotations

import asyncio
import logging
import signal
from uuid import UUID

import psycopg

from server.shared.turn_taking_v2 import PostgresTurnTakingV2Store

logger = logging.getLogger(__name__)


class TurnTakingV2Worker:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.store = PostgresTurnTakingV2Store(dsn)
        self._stop_event = asyncio.Event()

    async def run(self, recovery_interval_sec: float = 5.0) -> None:
        logger.info("Starting TurnTakingV2Worker...")
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop_event.set)
            except NotImplementedError:
                pass

        recovery_task = asyncio.create_task(
            self._recovery_loop(recovery_interval_sec)
        )

        try:
            while not self._stop_event.is_set():
                try:
                    async with await psycopg.AsyncConnection.connect(self.dsn, autocommit=True) as conn:
                        await conn.execute("LISTEN turn_taking_v2_observation")
                        logger.info("Listening on 'turn_taking_v2_observation'...")

                        while not self._stop_event.is_set():
                            try:
                                notify = await asyncio.wait_for(
                                    self._next_notification(conn),
                                    timeout=1.0,
                                )
                                if notify:
                                    obs_id_str = notify.payload
                                    try:
                                        obs_id = UUID(obs_id_str)
                                        await self._process_observation(obs_id)
                                    except ValueError:
                                        logger.warning("Invalid UUID payload: %s", obs_id_str)
                            except asyncio.TimeoutError:
                                continue
                            except psycopg.OperationalError:
                                logger.error("DB connection error in LISTEN loop. Reconnecting...")
                                break
                except Exception as e:
                    logger.error("Error in LISTEN loop: %s. Reconnecting in 2 seconds...", e)
                    await asyncio.sleep(2.0)

        finally:
            logger.info("Stopping TurnTakingV2Worker...")
            recovery_task.cancel()
            try:
                await recovery_task
            except asyncio.CancelledError:
                pass
            logger.info("TurnTakingV2Worker stopped.")

    async def _next_notification(self, conn: psycopg.AsyncConnection) -> psycopg.Notify | None:
        async for notify in conn.notifies():
            return notify
        return None

    async def _process_observation(self, observation_id: UUID) -> None:
        obs = await self.store.get_observation(observation_id)
        if obs is None:
            logger.warning("Observation not found: %s", observation_id)
            return

        from server.shared.db_pool import pooled_connection
        async with pooled_connection(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT 1 FROM turn_taking_v2_advisories WHERE observation_id = %s",
                    (observation_id,),
                )
                row = await cur.fetchone()
                if row is not None:
                    return

        logger.info("Processing observation %s: raw_text=%r", observation_id, obs.raw_text)

        proposal = "silence"
        if obs.raw_text and len(obs.raw_text.strip()) > 5:
            proposal = "prepare_only"

        await self.store.save_advisory(
            observation_id=observation_id,
            conversation_session_id=obs.conversation_session_id,
            turn_id=obs.turn_id,
            semantic_saturation=0.5,
            remaining_info_risk=0.5,
            semantic_split_risk=0.1,
            speech_decision_score=0.3,
            safe_response_level=1,
            proposal=proposal,
            confidence=0.5,
            reason=f"scaffold dummy for obs {observation_id}",
        )
        logger.info("Saved dummy advisory for observation %s. Proposal: %s", observation_id, proposal)

    async def _recovery_loop(self, interval_sec: float) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(interval_sec)
                from server.shared.db_pool import pooled_connection
                async with pooled_connection(self.dsn) as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            """
                            SELECT id FROM partial_transcript_observations o
                            WHERE NOT EXISTS (
                                SELECT 1 FROM turn_taking_v2_advisories a
                                WHERE a.observation_id = o.id
                            )
                            AND o.observed_at > now() - interval '1 hour'
                            ORDER BY o.observed_at ASC
                            LIMIT 50
                            """
                        )
                        rows = await cur.fetchall()
                        if rows:
                            logger.info("Recovery polling found %d unprocessed observations", len(rows))
                            for (obs_id,) in rows:
                                await self._process_observation(obs_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in recovery loop: %s", e)
