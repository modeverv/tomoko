from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from server.shared.inference.embedding.base import EmbeddingBackend
from server.shared.inference.router import InferenceRouter
from server.shared.models import SessionEvent

StopIntentMethod = Literal["rule", "embedding", "llm"]
StopIntentKind = Literal["hard_stop", "soft_stop", "withdraw", "defer", "accept", "none"]
StopIntentStatus = Literal["pending", "processing", "completed", "error"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StopIntentObservation:
    id: UUID
    transcript_id: str
    transcript_text: str
    rule_kind: str
    adopted_action: str
    conversation_session_id: UUID | None = None
    turn_id: str | None = None
    playback_state_json: dict[str, Any] = field(default_factory=dict)
    reply_state_json: dict[str, Any] = field(default_factory=dict)
    status: StopIntentStatus = "pending"
    attempts: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    locked_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


@dataclass(frozen=True)
class StopIntentSignal:
    observation_id: UUID
    method: StopIntentMethod
    predicted_kind: StopIntentKind
    confidence: float
    latency_ms: float
    model: str | None = None
    raw_reason_json: dict[str, Any] = field(default_factory=dict)
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class StopIntentStore(Protocol):
    async def insert_observation(self, observation: StopIntentObservation) -> None: ...

    async def claim_next_observation(
        self,
        *,
        stale_after: timedelta = timedelta(minutes=5),
    ) -> StopIntentObservation | None: ...

    async def record_signal(self, signal: StopIntentSignal) -> None: ...

    async def mark_completed(self, observation_id: UUID) -> None: ...

    async def mark_error(self, observation_id: UUID, error: str) -> None: ...


class InMemoryStopIntentStore:
    def __init__(self) -> None:
        self.observations: dict[UUID, StopIntentObservation] = {}
        self.signals: list[StopIntentSignal] = []
        self._lock = asyncio.Lock()

    async def insert_observation(self, observation: StopIntentObservation) -> None:
        async with self._lock:
            self.observations[observation.id] = observation

    async def claim_next_observation(
        self,
        *,
        stale_after: timedelta = timedelta(minutes=5),
    ) -> StopIntentObservation | None:
        del stale_after
        async with self._lock:
            pending = [
                observation
                for observation in self.observations.values()
                if observation.status == "pending"
            ]
            if not pending:
                return None
            observation = sorted(pending, key=lambda item: item.created_at)[0]
            claimed = _replace_observation(
                observation,
                status="processing",
                attempts=observation.attempts + 1,
                locked_at=datetime.now(UTC),
            )
            self.observations[claimed.id] = claimed
            return claimed

    async def record_signal(self, signal: StopIntentSignal) -> None:
        async with self._lock:
            self.signals.append(signal)

    async def mark_completed(self, observation_id: UUID) -> None:
        async with self._lock:
            observation = self.observations[observation_id]
            self.observations[observation_id] = _replace_observation(
                observation,
                status="completed",
                completed_at=datetime.now(UTC),
                error=None,
            )

    async def mark_error(self, observation_id: UUID, error: str) -> None:
        async with self._lock:
            observation = self.observations[observation_id]
            self.observations[observation_id] = _replace_observation(
                observation,
                status="error",
                completed_at=datetime.now(UTC),
                error=error,
            )


class PostgresStopIntentStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def insert_observation(self, observation: StopIntentObservation) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO stop_intent_observations (
                        id,
                        conversation_session_id,
                        turn_id,
                        transcript_id,
                        transcript_text,
                        rule_kind,
                        adopted_action,
                        playback_state_json,
                        reply_state_json,
                        status,
                        attempts,
                        created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', 0, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        observation.id,
                        observation.conversation_session_id,
                        observation.turn_id,
                        observation.transcript_id,
                        observation.transcript_text,
                        observation.rule_kind,
                        observation.adopted_action,
                        Jsonb(observation.playback_state_json),
                        Jsonb(observation.reply_state_json),
                        observation.created_at,
                    ),
                )

    async def claim_next_observation(
        self,
        *,
        stale_after: timedelta = timedelta(minutes=5),
    ) -> StopIntentObservation | None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        WITH candidate AS (
                            SELECT id
                            FROM stop_intent_observations
                            WHERE status = 'pending'
                               OR (
                                    status = 'processing'
                                    AND locked_at < now() - (%s::interval)
                               )
                            ORDER BY created_at ASC
                            LIMIT 1
                            FOR UPDATE SKIP LOCKED
                        )
                        UPDATE stop_intent_observations o
                        SET status = 'processing',
                            attempts = attempts + 1,
                            locked_at = now(),
                            error = NULL
                        FROM candidate
                        WHERE o.id = candidate.id
                        RETURNING o.id, o.conversation_session_id, o.turn_id,
                                  o.transcript_id, o.transcript_text, o.rule_kind,
                                  o.adopted_action, o.playback_state_json,
                                  o.reply_state_json, o.status, o.attempts,
                                  o.created_at, o.locked_at, o.completed_at, o.error
                        """,
                        (f"{int(stale_after.total_seconds())} seconds",),
                    )
                    row = await cur.fetchone()
        return _observation_from_row(row) if row is not None else None

    async def record_signal(self, signal: StopIntentSignal) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO stop_intent_shadow_signals (
                        id,
                        observation_id,
                        method,
                        model,
                        predicted_kind,
                        confidence,
                        latency_ms,
                        raw_reason_json,
                        created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        signal.id,
                        signal.observation_id,
                        signal.method,
                        signal.model,
                        signal.predicted_kind,
                        signal.confidence,
                        signal.latency_ms,
                        Jsonb(signal.raw_reason_json),
                        signal.created_at,
                    ),
                )

    async def mark_completed(self, observation_id: UUID) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE stop_intent_observations
                    SET status = 'completed',
                        completed_at = now(),
                        error = NULL
                    WHERE id = %s
                    """,
                    (observation_id,),
                )

    async def mark_error(self, observation_id: UUID, error: str) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE stop_intent_observations
                    SET status = 'error',
                        completed_at = now(),
                        error = %s
                    WHERE id = %s
                    """,
                    (error[:500], observation_id),
                )


class RuleStopIntentClassifier:
    async def classify(self, observation: StopIntentObservation) -> StopIntentSignal:
        started_at = time.perf_counter()
        predicted_kind = _kind_from_rule(observation.rule_kind, observation.adopted_action)
        confidence = 1.0 if predicted_kind != "none" else 0.2
        return StopIntentSignal(
            observation_id=observation.id,
            method="rule",
            predicted_kind=predicted_kind,
            confidence=confidence,
            latency_ms=(time.perf_counter() - started_at) * 1000,
            model="deterministic-rule-v1",
            raw_reason_json={
                "rule_kind": observation.rule_kind,
                "adopted_action": observation.adopted_action,
            },
        )


class EmbeddingStopIntentClassifier:
    def __init__(self, embedding_backend: EmbeddingBackend | None = None) -> None:
        self.embedding_backend = embedding_backend
        self.model = getattr(embedding_backend, "model", "lexical-stop-intent-v1")

    async def classify(self, observation: StopIntentObservation) -> StopIntentSignal:
        started_at = time.perf_counter()
        text = observation.transcript_text
        predicted_kind, confidence, phrase = _lexical_stop_prediction(text)
        if self.embedding_backend is not None:
            confidence = max(confidence, await self._embedding_confidence(text))
            if confidence >= 0.84 and predicted_kind == "none":
                predicted_kind = "soft_stop"
        return StopIntentSignal(
            observation_id=observation.id,
            method="embedding",
            predicted_kind=predicted_kind,
            confidence=confidence,
            latency_ms=(time.perf_counter() - started_at) * 1000,
            model=self.model,
            raw_reason_json={"matched_phrase": phrase},
        )

    async def _embedding_confidence(self, text: str) -> float:
        phrases = ["その話はいったん置いといて", "今は聞けない", "あとにして"]
        try:
            query = await self.embedding_backend.embed_query(text)  # type: ignore[union-attr]
            scores: list[float] = []
            for phrase in phrases:
                phrase_embedding = await self.embedding_backend.embed_query(phrase)  # type: ignore[union-attr]
                scores.append(_cosine(query, phrase_embedding))
            return max(scores) if scores else 0.0
        except Exception as exc:
            logger.info("embedding stop-intent classifier degraded error=%s", exc)
            return 0.0


class LLMStopIntentClassifier:
    def __init__(
        self,
        router: InferenceRouter,
        *,
        role: str = "candidate_gen",
        model_name: str = "local-json-stop-intent",
    ) -> None:
        self.router = router
        self.role = role
        self.model_name = model_name

    async def classify(self, observation: StopIntentObservation) -> StopIntentSignal:
        started_at = time.perf_counter()
        backend = await self.router.select(self.role, "privacy")
        chunks: list[str] = []
        async for chunk in backend.chat_stream(
            _STOP_INTENT_SYSTEM_PROMPT,
            [{"role": "user", "content": observation.transcript_text}],
        ):
            chunks.append(chunk)
        parsed = _parse_llm_json("".join(chunks))
        return StopIntentSignal(
            observation_id=observation.id,
            method="llm",
            predicted_kind=parsed["predicted_kind"],
            confidence=parsed["confidence"],
            latency_ms=(time.perf_counter() - started_at) * 1000,
            model=getattr(backend, "name", self.model_name),
            raw_reason_json={"reason": parsed.get("reason", "")[:160]},
        )


class StopIntentClassifierWorker:
    def __init__(
        self,
        *,
        store: StopIntentStore,
        rule_classifier: RuleStopIntentClassifier | None = None,
        embedding_classifier: EmbeddingStopIntentClassifier | None = None,
        llm_classifier: LLMStopIntentClassifier | None = None,
        result_callback: Callable[[SessionEvent], Awaitable[None]] | None = None,
        poll_interval_sec: float = 0.2,
    ) -> None:
        self.store = store
        self.rule_classifier = rule_classifier or RuleStopIntentClassifier()
        self.embedding_classifier = embedding_classifier or EmbeddingStopIntentClassifier()
        self.llm_classifier = llm_classifier
        self.result_callback = result_callback
        self.poll_interval_sec = poll_interval_sec
        self._llm_semaphore = asyncio.Semaphore(1)
        self._running = False
        self._processed_count = 0
        self._error_count = 0
        self._latencies_ms: list[float] = []

    async def run_forever(self) -> None:
        self._running = True
        try:
            while self._running:
                processed = await self.process_once()
                if not processed:
                    await asyncio.sleep(self.poll_interval_sec)
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False

    async def process_once(self) -> bool:
        observation = await self.store.claim_next_observation()
        if observation is None:
            return False
        started_at = time.perf_counter()
        try:
            signals: list[StopIntentSignal] = []
            for signal in [
                await self.rule_classifier.classify(observation),
                await self.embedding_classifier.classify(observation),
            ]:
                await self._record_and_emit_signal(observation, signal)
                signals.append(signal)
            if self.llm_classifier is not None:
                async with self._llm_semaphore:
                    llm_signal = await self._classify_llm_optional(observation)
                await self._record_and_emit_signal(observation, llm_signal)
                signals.append(llm_signal)
            await self.store.mark_completed(observation.id)
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            self._record_latency(elapsed_ms)
            logger.info(
                "stop-intent observation processed id=%s signals=%s elapsed_ms=%.1f "
                "processed_count=%s avg_latency_ms=%.1f p95_latency_ms=%.1f "
                "error_count=%s",
                observation.id,
                len(signals),
                elapsed_ms,
                self._processed_count,
                self._avg_latency_ms(),
                self._p95_latency_ms(),
                self._error_count,
            )
            return True
        except Exception as exc:
            self._error_count += 1
            await self.store.mark_error(observation.id, str(exc))
            logger.warning(
                "stop-intent observation failed id=%s error=%s error_count=%s",
                observation.id,
                exc,
                self._error_count,
            )
            return True

    async def _record_and_emit_signal(
        self,
        observation: StopIntentObservation,
        signal: StopIntentSignal,
    ) -> None:
        await self.store.record_signal(signal)
        await self._emit_advisory_result(observation, signal)

    async def _classify_llm_optional(
        self,
        observation: StopIntentObservation,
    ) -> StopIntentSignal:
        assert self.llm_classifier is not None
        started_at = time.perf_counter()
        try:
            return await self.llm_classifier.classify(observation)
        except Exception as exc:
            self._error_count += 1
            logger.warning(
                "stop-intent llm classifier degraded id=%s error=%s error_count=%s",
                observation.id,
                exc,
                self._error_count,
            )
            return StopIntentSignal(
                observation_id=observation.id,
                method="llm",
                predicted_kind="none",
                confidence=0.0,
                latency_ms=(time.perf_counter() - started_at) * 1000,
                model=self.llm_classifier.model_name,
                raw_reason_json={
                    "degraded": True,
                    "error": str(exc)[:300],
                },
            )

    async def _emit_advisory_result(
        self,
        observation: StopIntentObservation,
        signal: StopIntentSignal,
    ) -> None:
        if self.result_callback is None:
            return
        await self.result_callback(
            SessionEvent(
                type="stop_intent_classified",
                payload={
                    "observation_id": str(observation.id),
                    "turn_id": observation.turn_id,
                    "transcript_id": observation.transcript_id,
                    "method": signal.method,
                    "predicted_kind": signal.predicted_kind,
                    "confidence": signal.confidence,
                    "latency_ms": signal.latency_ms,
                },
            )
        )

    def _record_latency(self, elapsed_ms: float) -> None:
        self._processed_count += 1
        self._latencies_ms.append(elapsed_ms)
        if len(self._latencies_ms) > 200:
            self._latencies_ms = self._latencies_ms[-200:]

    def _avg_latency_ms(self) -> float:
        if not self._latencies_ms:
            return 0.0
        return sum(self._latencies_ms) / len(self._latencies_ms)

    def _p95_latency_ms(self) -> float:
        if not self._latencies_ms:
            return 0.0
        ordered = sorted(self._latencies_ms)
        index = max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))
        return ordered[index]


def should_record_stop_intent_candidate(text: str) -> bool:
    predicted_kind, confidence, _phrase = _lexical_stop_prediction(text)
    return predicted_kind != "none" and confidence >= 0.55


def should_adopt_stop_signal(kind: str, confidence: float) -> bool:
    if kind == "hard_stop":
        return confidence >= 0.8
    if kind in {"soft_stop", "withdraw"}:
        return confidence >= 0.88
    return False


def build_stop_observation(
    *,
    transcript_text: str,
    transcript_id: str | None = None,
    conversation_session_id: UUID | None,
    turn_id: str | None,
    rule_kind: str,
    adopted_action: str,
    playback_state_json: dict[str, Any],
    reply_state_json: dict[str, Any],
) -> StopIntentObservation:
    return StopIntentObservation(
        id=uuid4(),
        transcript_id=transcript_id or uuid4().hex,
        transcript_text=transcript_text,
        conversation_session_id=conversation_session_id,
        turn_id=turn_id,
        rule_kind=rule_kind,
        adopted_action=adopted_action,
        playback_state_json=playback_state_json,
        reply_state_json=reply_state_json,
    )


def _kind_from_rule(rule_kind: str, adopted_action: str) -> StopIntentKind:
    if rule_kind == "hard_interrupt" or adopted_action == "restart_turn":
        return "hard_stop"
    if rule_kind in {"withdraw", "withdraw_rule"} or adopted_action == "withdraw":
        return "withdraw"
    if rule_kind in {"defer", "soft_interrupt"}:
        return "defer"
    if rule_kind == "accept":
        return "accept"
    if rule_kind == "stop_candidate":
        return "soft_stop"
    return "none"


def _lexical_stop_prediction(text: str) -> tuple[StopIntentKind, float, str | None]:
    normalized = _normalize(text)
    phrase_map: tuple[tuple[str, StopIntentKind, float], ...] = (
        ("ストップ", "hard_stop", 0.98),
        ("止めて", "hard_stop", 0.98),
        ("やめて", "hard_stop", 0.96),
        ("停止", "hard_stop", 0.95),
        ("静かにして", "withdraw", 0.96),
        ("話さないで", "withdraw", 0.94),
        ("黙ってて", "withdraw", 0.94),
        ("置いといて", "soft_stop", 0.9),
        ("いったん置いて", "soft_stop", 0.9),
        ("今は聞けない", "soft_stop", 0.9),
        ("あとにして", "defer", 0.86),
        ("後にして", "defer", 0.86),
        ("今じゃない", "defer", 0.82),
        ("言って", "accept", 0.72),
    )
    for phrase, kind, confidence in phrase_map:
        if _normalize(phrase) in normalized:
            return kind, confidence, phrase
    return "none", 0.0, None


def _normalize(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _parse_llm_json(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end < start:
            raise ValueError("LLM stop-intent result is not JSON") from exc
        payload = json.loads(raw[start : end + 1])
    kind = str(payload.get("predicted_kind", "none"))
    if kind not in {"hard_stop", "soft_stop", "withdraw", "defer", "accept", "none"}:
        kind = "none"
    confidence = max(0.0, min(1.0, float(payload.get("confidence", 0.0))))
    return {
        "predicted_kind": kind,
        "confidence": confidence,
        "reason": str(payload.get("reason", "")),
    }


def _observation_from_row(row: tuple[Any, ...]) -> StopIntentObservation:
    return StopIntentObservation(
        id=row[0],
        conversation_session_id=row[1],
        turn_id=row[2],
        transcript_id=row[3],
        transcript_text=row[4],
        rule_kind=row[5],
        adopted_action=row[6],
        playback_state_json=dict(row[7] or {}),
        reply_state_json=dict(row[8] or {}),
        status=row[9],
        attempts=int(row[10]),
        created_at=row[11],
        locked_at=row[12],
        completed_at=row[13],
        error=row[14],
    )


def _replace_observation(
    observation: StopIntentObservation,
    **changes,
) -> StopIntentObservation:
    values = {
        "id": observation.id,
        "conversation_session_id": observation.conversation_session_id,
        "turn_id": observation.turn_id,
        "transcript_id": observation.transcript_id,
        "transcript_text": observation.transcript_text,
        "rule_kind": observation.rule_kind,
        "adopted_action": observation.adopted_action,
        "playback_state_json": observation.playback_state_json,
        "reply_state_json": observation.reply_state_json,
        "status": observation.status,
        "attempts": observation.attempts,
        "created_at": observation.created_at,
        "locked_at": observation.locked_at,
        "completed_at": observation.completed_at,
        "error": observation.error,
    }
    values.update(changes)
    return StopIntentObservation(**values)


_STOP_INTENT_SYSTEM_PROMPT = """You classify Japanese user utterances for stop intent.
Return JSON only:
{"predicted_kind":"hard_stop|soft_stop|withdraw|defer|accept|none","confidence":0.0,"reason":"short"}
Do not issue commands. Do not include free text outside JSON."""
