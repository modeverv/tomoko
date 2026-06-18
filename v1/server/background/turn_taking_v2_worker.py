from __future__ import annotations

import asyncio
import json
import logging
import signal
import time
from typing import Any
from uuid import UUID

import psycopg

from server.shared.inference.router import InferenceRouter
from server.shared.inference.trace import chat_stream_with_trace_role
from server.shared.turn_taking_v2 import PostgresTurnTakingV2Store

logger = logging.getLogger(__name__)


class TurnTakingV2Worker:
    def __init__(self, dsn: str, router: InferenceRouter | None = None) -> None:
        self.dsn = dsn
        self.store = PostgresTurnTakingV2Store(dsn)
        self.router = router
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
                    async with await psycopg.AsyncConnection.connect(
                        self.dsn,
                        autocommit=True,
                    ) as conn:
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
                            except TimeoutError:
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
            SemanticFinishJudge,
            SpeechMotivationEvaluator,
            StablePrefixExtractor,
            TranscriptValidity,
        )

        is_valid = TranscriptValidity.evaluate(obs.raw_text)
        if not is_valid:
            logger.info(
                "Observation %s ignored as hallucination/noise: raw_text=%r",
                observation_id,
                obs.raw_text,
            )
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
                would_start_inference_fusion=False,
                fusion_score=0.0,
            )
            from server.shared.turn_taking_logger import log_v2_shadow_advisory

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
                p_yielding=obs.p_yielding,
                fusion_score=0.0,
                would_start_inference_fusion=False,
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
        # fusion 用: 現テキストが stable prefix と一致 = 再送で確定済み（揺らぎなし）
        tail_stable = bool(stable_text) and stable_text == obs.raw_text

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

        semantic_result = None
        if self.router and "local_gemma4_e2b_mlx" in self.router.backends:
            backend = self.router.backends["local_gemma4_e2b_mlx"]
            try:
                logger.info(
                    "[v2_shadow] Requesting compact semantic LLM (Gemma-4 E2B) "
                    "for raw_text=%r",
                    obs.raw_text,
                )
                semantic_result = await _run_compact_semantic_finish_judge(
                    backend,
                    obs.raw_text,
                )
                logger.info("[v2_shadow] LLM semantic judgment succeeded: %s", semantic_result)
            except Exception as e:
                logger.error(
                    "[v2_shadow] Failed to run compact LLM semantic finish judge: "
                    "%s. Falling back to rule-based.",
                    e,
                )
                semantic_result = None

        if semantic_result is None:
            semantic_result = SemanticFinishJudge.evaluate(obs.raw_text)

        motivation_result = SpeechMotivationEvaluator.evaluate(
            semantic_saturation=semantic_result["semantic_saturation"],
            remaining_info_risk=semantic_result["remaining_info_risk"],
            semantic_split_risk=semantic_result["semantic_split_risk"],
            confidence=semantic_result["confidence"],
            vad_state=obs.vad_state,
            attention_mode=obs.attention_mode,
            audio_level_db=obs.audio_level_db,
            p_yielding=obs.p_yielding,
            tail_stable=tail_stable,
        )

        reason = (
            f"valid_speech: saturation={semantic_result['semantic_saturation']}, "
            f"split_risk={semantic_result['semantic_split_risk']}, "
            f"score={motivation_result['speech_decision_score']}"
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
            would_start_inference_fusion=motivation_result.get("would_start_inference_fusion"),
            fusion_score=motivation_result.get("fusion_score"),
        )
        logger.info(
            "Saved advisory for observation %s. Proposal: %s, Score: %s, Stable: %r",
            observation_id,
            motivation_result["proposal"],
            motivation_result["speech_decision_score"],
            stable_text,
        )
        from server.shared.turn_taking_logger import log_v2_shadow_advisory

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
            p_yielding=obs.p_yielding,
            fusion_score=motivation_result.get("fusion_score"),
            would_start_inference_fusion=motivation_result.get("would_start_inference_fusion"),
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
                            logger.info(
                                "Recovery polling found %d unprocessed observations",
                                len(rows),
                            )
                            for (obs_id,) in rows:
                                await self._process_observation(obs_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in recovery loop: %s", e)


async def _run_compact_semantic_finish_judge(
    backend: Any,
    raw_text: str,
) -> dict[str, float]:
    full_response = ""
    async for chunk in chat_stream_with_trace_role(
        backend,
        _compact_semantic_system_prompt(),
        [{"role": "user", "content": _compact_semantic_user_prompt(raw_text)}],
        max_tokens=48,
        trace_role="turn_taking_v2",
    ):
        full_response += chunk

    logger.info("[v2_shadow] Compact LLM Response: %r", full_response)
    semantic_saturation, remaining_info_risk = _parse_compact_semantic_response(
        full_response
    )
    return _semantic_result_from_scores(
        semantic_saturation=semantic_saturation,
        remaining_info_risk=remaining_info_risk,
    )


def _compact_semantic_system_prompt() -> str:
    return (
        "You are a JSON extraction engine for Japanese speech turn-taking. "
        'Return only one JSON object like {"semantic_saturation": 0.0, '
        '"remaining_info_risk": 1.0}. Do not copy a schema. Do not include '
        "Markdown or explanations. Values must be numbers between 0 and 1."
    )


def _compact_semantic_user_prompt(raw_text: str) -> str:
    return "\n".join(
        [
            "日本語発話の完了度を数値化してください。",
            f"発話: {json.dumps(raw_text, ensure_ascii=False)}",
            "",
            "semantic_saturation: 発話が文末として意味的に完了しているほど高い。",
            "remaining_info_risk: まだ後続の語句や説明が続きそうなほど高い。",
            "出力は値入りJSONだけ。schemaや説明は返さない。",
        ]
    )


def _parse_compact_semantic_response(raw_response: str) -> tuple[float, float]:
    json_text = _extract_json_object(raw_response)
    if json_text is None:
        raise ValueError("semantic LLM response did not contain a JSON object")

    parsed = json.loads(json_text)
    expected_keys = {"semantic_saturation", "remaining_info_risk"}
    if not isinstance(parsed, dict) or set(parsed) != expected_keys:
        raise ValueError("semantic LLM response did not match the strict 2-key shape")

    semantic_saturation = float(parsed["semantic_saturation"])
    remaining_info_risk = float(parsed["remaining_info_risk"])
    if not (
        0.0 <= semantic_saturation <= 1.0
        and 0.0 <= remaining_info_risk <= 1.0
    ):
        raise ValueError("semantic LLM scores must be between 0.0 and 1.0")

    return semantic_saturation, remaining_info_risk


def _extract_json_object(raw_response: str) -> str | None:
    stripped = raw_response.strip()
    if not stripped:
        return None
    if "```json" in stripped:
        return stripped.split("```json", 1)[1].split("```", 1)[0].strip()
    if "```" in stripped:
        return stripped.split("```", 1)[1].split("```", 1)[0].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return stripped[start : end + 1]


def _semantic_result_from_scores(
    *,
    semantic_saturation: float,
    remaining_info_risk: float,
) -> dict[str, float]:
    if semantic_saturation >= 0.90:
        safe_response_level = 5
    elif semantic_saturation >= 0.75:
        safe_response_level = 4
    elif semantic_saturation >= 0.50:
        safe_response_level = 3
    elif semantic_saturation >= 0.30:
        safe_response_level = 2
    else:
        safe_response_level = 1

    return {
        "semantic_saturation": semantic_saturation,
        "remaining_info_risk": remaining_info_risk,
        "semantic_split_risk": 0.0,
        "safe_response_level": safe_response_level,
        "confidence": 0.8,
    }
