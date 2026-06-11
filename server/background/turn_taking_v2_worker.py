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

        from server.gateway.turn_taking.v2_evaluator import (
            TranscriptValidity,
            StablePrefixExtractor,
            SemanticFinishJudge,
            SpeechMotivationEvaluator,
        )

        is_valid = TranscriptValidity.evaluate(obs.raw_text)
        if not is_valid:
            logger.info("Observation %s ignored as hallucination/noise: raw_text=%r", observation_id, obs.raw_text)
            await self.store.save_advisory(
                observation_id=observation_id,
                conversation_session_id=obs.conversation_session_id,
                turn_id=obs.turn_id,
                semantic_saturation=0.0,
                remaining_info_risk=1.0,
                semantic_split_risk=0.0,
                speech_decision_score=0.0,
                safe_response_level=0,
                proposal="silence",
                confidence=0.0,
                would_start_inference=False,
                reason="hallucination_or_noise",
            )
            from server.shared.turn_taking_logger import log_v2_shadow_advisory
            import time
            log_v2_shadow_advisory(
                ts_ms=int(time.time() * 1000),
                conversation_session_id=obs.conversation_session_id,
                turn_id=obs.turn_id,
                partial_revision=obs.revision,
                stable_text=None,
                semantic_saturation=0.0,
                remaining_info_risk=1.0,
                semantic_split_risk=0.0,
                speech_decision_score=0.0,
                proposal="silence",
                confidence=0.0,
                would_start_inference=False,
                reason="hallucination_or_noise",
            )
            return

        history_before = await self.store.get_turn_history(
            conversation_session_id=obs.conversation_session_id,
            turn_id=obs.turn_id,
            before_revision=obs.revision,
        )

        stable_text, unstable_tail = StablePrefixExtractor.split_stable_unstable(
            history_before, obs.raw_text
        )

        async with pooled_connection(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE partial_transcript_observations
                    SET stable_text = %s,
                        unstable_tail = %s
                    WHERE id = %s
                    """,
                    (stable_text, unstable_tail, observation_id),
                )

        semantic_result = SemanticFinishJudge.evaluate(obs.raw_text)

        motivation_result = SpeechMotivationEvaluator.evaluate(
            semantic_saturation=semantic_result["semantic_saturation"],
            remaining_info_risk=semantic_result["remaining_info_risk"],
            semantic_split_risk=semantic_result["semantic_split_risk"],
            confidence=semantic_result["confidence"],
            vad_state=obs.vad_state,
            attention_mode=obs.attention_mode,
            audio_level_db=obs.audio_level_db,
        )

        reason = (
            f"valid_speech: saturation={semantic_result['semantic_saturation']}, "
            f"split_risk={semantic_result['semantic_split_risk']}, score={motivation_result['speech_decision_score']}"
        )

        await self.store.save_advisory(
            observation_id=observation_id,
            conversation_session_id=obs.conversation_session_id,
            turn_id=obs.turn_id,
            semantic_saturation=semantic_result["semantic_saturation"],
            remaining_info_risk=semantic_result["remaining_info_risk"],
            semantic_split_risk=semantic_result["semantic_split_risk"],
            speech_decision_score=motivation_result["speech_decision_score"],
            safe_response_level=semantic_result["safe_response_level"],
            proposal=motivation_result["proposal"],
            confidence=semantic_result["confidence"],
            would_start_inference=motivation_result.get("would_start_inference"),
            reason=reason,
        )
        logger.info(
            "Saved advisory for observation %s. Proposal: %s, Score: %s, Stable: %r",
            observation_id,
            motivation_result["proposal"],
            motivation_result["speech_decision_score"],
            stable_text,
        )
        from server.shared.turn_taking_logger import log_v2_shadow_advisory
        import time
        log_v2_shadow_advisory(
            ts_ms=int(time.time() * 1000),
            conversation_session_id=obs.conversation_session_id,
            turn_id=obs.turn_id,
            partial_revision=obs.revision,
            stable_text=stable_text,
            semantic_saturation=semantic_result["semantic_saturation"],
            remaining_info_risk=semantic_result["remaining_info_risk"],
            semantic_split_risk=semantic_result["semantic_split_risk"],
            speech_decision_score=motivation_result["speech_decision_score"],
            proposal=motivation_result["proposal"],
            confidence=semantic_result["confidence"],
            would_start_inference=motivation_result.get("would_start_inference"),
            reason=reason,
        )

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
