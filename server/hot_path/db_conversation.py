from __future__ import annotations

import asyncio
import math
import struct
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from server.audio.stt import AppleSpeechStreamingBackend, StreamingSttEvent, observation_events
from server.audio.vad import VADProcessor
from server.hot_path.audio_conversation import (
    HotPathConversationResult,
    StreamingSttBackend,
)
from server.hot_path.model_executor import PromptExecutionResult, TtsBackend
from server.hot_path.speech_executor import SpeechOrderExecutor, prompt_request_for_order
from server.shared.models import (
    AudioSpeechSegment,
    ModelOutputEvent,
    PartialTranscriptObservation,
    SpeechOrder,
)
from server.shared.notify import parse_id_payload
from server.tomoko.db_bridge import (
    SqlCommand,
    insert_audio_output_event_sql,
    insert_prompt_request_for_order_sql,
    insert_stt_observation_sql,
    notify_stt_observation_sql,
    speech_order_from_row,
)


@dataclass(slots=True)
class DbSplitTiming:
    speech_end_to_stt_ms: float = 0.0
    stt_to_notify_ms: float = 0.0
    notify_to_order_ms: float = 0.0
    order_to_first_audio_ms: float | None = None
    total_ms: float = 0.0


@dataclass(slots=True)
class HotPathDbSplitConversation:
    dsn: str
    vad: VADProcessor
    stt_backend: StreamingSttBackend
    speech_executor: SpeechOrderExecutor
    speech_rms_threshold: float = 0.02
    order_timeout_sec: float = 30.0
    recovery_poll_interval_sec: float = 0.05
    _audio_clock_ms: float = field(default_factory=lambda: time.time() * 1000.0)
    _listener: psycopg.AsyncConnection[object] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _conn: psycopg.AsyncConnection[dict[str, object]] | None = field(
        default=None,
        init=False,
        repr=False,
    )

    async def process_audio_bytes(self, payload: bytes) -> HotPathConversationResult | None:
        return await self.process_audio_samples(audio_bytes_to_samples(payload))

    async def process_audio_samples(
        self,
        samples: tuple[float, ...],
    ) -> HotPathConversationResult | None:
        if not samples:
            return None
        now_ms = self._audio_clock_ms
        self._audio_clock_ms += len(samples) / self.vad.sample_rate * 1000.0
        segment = self.vad.process_chunk(
            samples,
            speech_probability=speech_probability_from_rms(
                samples,
                threshold=self.speech_rms_threshold,
            ),
            now_ms=now_ms,
        )
        if segment is None:
            return None
        _console_event(
            "vad_segment",
            samples=len(segment.samples),
            sample_rate=segment.sample_rate,
            started_at=segment.started_at.isoformat(),
            ended_at=segment.ended_at.isoformat(),
        )
        return await self.process_segment(segment)

    async def process_segment(self, segment: AudioSpeechSegment) -> HotPathConversationResult:
        started = time.perf_counter()
        _console_event("stt_start", samples=len(segment.samples))
        observations = await observation_events(segment, self.stt_backend)
        stt_done = time.perf_counter()
        final_observation = next(
            (observation for observation in observations if observation.is_final),
            observations[-1] if observations else None,
        )
        if final_observation is None:
            return HotPathConversationResult(
                observations=observations,
                durable_utterance=None,
                context_snapshot=None,
                prompt_request=None,
                execution_result=PromptExecutionResult(),
            )
        order, order_received_at = await self._notify_and_wait_for_order(final_observation)
        request = prompt_request_for_order(order)
        execution_result = PromptExecutionResult(
            model_events=[
                ModelOutputEvent(
                    request_id=order.id,
                    event_kind="complete",
                    text=order.text,
                    trace_id=order.trace_id,
                )
            ]
        )
        audio_result = await self.speech_executor.execute(order)
        execution_result.audio_chunks.extend(audio_result.audio_chunks)
        await self._record_audio(order, execution_result)
        first_audio_ms = None
        if execution_result.audio_chunks:
            first_audio_ms = (time.perf_counter() - order_received_at) * 1000.0
        timing = DbSplitTiming(
            speech_end_to_stt_ms=(stt_done - started) * 1000.0,
            stt_to_notify_ms=0.0,
            notify_to_order_ms=(order_received_at - stt_done) * 1000.0,
            order_to_first_audio_ms=first_audio_ms,
            total_ms=(time.perf_counter() - started) * 1000.0,
        )
        _console_event(
            "db_split_complete",
            order_id=str(order.id),
            notify_to_order_ms=round(timing.notify_to_order_ms, 3),
            order_to_first_audio_ms=(
                round(timing.order_to_first_audio_ms, 3)
                if timing.order_to_first_audio_ms is not None
                else None
            ),
            total_ms=round(timing.total_ms, 3),
        )
        return HotPathConversationResult(
            observations=observations,
            durable_utterance=None,
            context_snapshot=None,
            prompt_request=request,
            execution_result=execution_result,
            speech_order=order,
        )

    async def _notify_and_wait_for_order(
        self,
        observation: PartialTranscriptObservation,
    ) -> tuple[SpeechOrder, float]:
        listener, conn = await self._ensure_connections()
        await execute_command(conn, insert_stt_observation_sql(observation))
        query, params = notify_stt_observation_sql(observation.id)
        await conn.execute(query, params)
        _console_event(
            "stt_observation_notified",
            observation_id=str(observation.id),
            trace_id=str(observation.trace_id),
        )
        deadline = time.monotonic() + self.order_timeout_sec
        while time.monotonic() < deadline:
            async for notify in listener.notifies(timeout=0.1, stop_after=1):
                order_id = parse_id_payload(notify.payload)
                order = await self._load_speech_order(order_id)
                if order is not None and order.trace_id == observation.trace_id:
                    return order, time.perf_counter()
            recovered = await self._poll_speech_order_by_trace(observation.trace_id)
            if recovered is not None:
                return recovered, time.perf_counter()
            await asyncio.sleep(self.recovery_poll_interval_sec)
        raise TimeoutError(f"speech order was not received for {observation.id}")

    async def _ensure_connections(
        self,
    ) -> tuple[psycopg.AsyncConnection[object], psycopg.AsyncConnection[dict[str, object]]]:
        if self._listener is None or self._listener.closed:
            self._listener = await psycopg.AsyncConnection.connect(
                self.dsn,
                autocommit=True,
            )
            await self._listener.execute("LISTEN v2_speech_order")
            _console_event("listen_start", channel="v2_speech_order")
        if self._conn is None or self._conn.closed:
            self._conn = await psycopg.AsyncConnection.connect(
                self.dsn,
                autocommit=True,
                row_factory=dict_row,
            )
            _console_event("db_connection_ready")
        return self._listener, self._conn

    async def warm_connections(self) -> None:
        await self._ensure_connections()

    async def aclose(self) -> None:
        if self._listener is not None:
            await self._listener.close()
            self._listener = None
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _load_speech_order(self, order_id: UUID) -> SpeechOrder | None:
        _, conn = await self._ensure_connections()
        cursor = await conn.execute(
            """
            SELECT
                id,
                scheduler_decision_id,
                text,
                mode,
                reason,
                priority,
                supersedes_order_id,
                trace_id,
                created_at
            FROM v2_speech_orders
            WHERE id = %s
            """,
            (order_id,),
        )
        row = await cursor.fetchone()
        return speech_order_from_row(row) if row is not None else None

    async def _poll_speech_order_by_trace(self, trace_id: UUID) -> SpeechOrder | None:
        _, conn = await self._ensure_connections()
        cursor = await conn.execute(
            """
            SELECT
                id,
                scheduler_decision_id,
                text,
                mode,
                reason,
                priority,
                supersedes_order_id,
                trace_id,
                created_at
            FROM v2_speech_orders
            WHERE trace_id = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (trace_id,),
        )
        row = await cursor.fetchone()
        return speech_order_from_row(row) if row is not None else None

    async def _record_audio(
        self,
        order: SpeechOrder,
        result: PromptExecutionResult,
    ) -> None:
        _, conn = await self._ensure_connections()
        await execute_command(conn, insert_prompt_request_for_order_sql(order))
        for chunk in result.audio_chunks:
            await execute_command(conn, insert_audio_output_event_sql(chunk))


def create_default_db_split_conversation(
    dsn: str,
    tts_backend: TtsBackend,
) -> HotPathDbSplitConversation:
    return HotPathDbSplitConversation(
        dsn=dsn,
        vad=VADProcessor(),
        stt_backend=AppleSpeechStreamingBackend(),
        speech_executor=SpeechOrderExecutor(tts_backend),
    )


class StaticStreamingSttBackend:
    def __init__(self, events: list[StreamingSttEvent]) -> None:
        self._events = events

    async def transcribe_stream(
        self,
        _segment: AudioSpeechSegment,
    ) -> AsyncIterator[StreamingSttEvent]:
        for event in self._events:
            yield event


async def execute_command(
    conn: psycopg.AsyncConnection[dict[str, object]],
    command: SqlCommand,
) -> None:
    await conn.execute(command.query, command.params)


def audio_bytes_to_samples(payload: bytes) -> tuple[float, ...]:
    sample_count = len(payload) // 4
    if sample_count <= 0:
        return ()
    return struct.unpack(f"<{sample_count}f", payload[: sample_count * 4])


def speech_probability_from_rms(samples: tuple[float, ...], *, threshold: float) -> float:
    if not samples or threshold <= 0:
        return 0.0
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    return min(1.0, rms / threshold)


def _console_event(event: str, **fields: object) -> None:
    parts = [f"[tomoko:hot-path-db] {event}"]
    for key, value in fields.items():
        text = str(value)
        if len(text) > 120:
            text = text[:117] + "..."
        parts.append(f"{key}={text!r}")
    print(" ".join(parts), flush=True)
