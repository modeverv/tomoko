from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from server.shared.models import (
    AudioChunkOut,
    CancelPolicy,
    PartialTranscriptObservation,
    PromptRequest,
    PromptScope,
    SemanticSaturationResult,
    SpeechOrder,
    SpeechOrderMode,
    SpeechSchedulerOutput,
)
from server.shared.notify import notify_sql


@dataclass(frozen=True, slots=True)
class SqlCommand:
    query: str
    params: tuple[Any, ...]


def insert_stt_observation_sql(observation: PartialTranscriptObservation) -> SqlCommand:
    return SqlCommand(
        """
        INSERT INTO v2_stt_observations (
            id,
            event_kind,
            text,
            is_final,
            stability,
            audio_started_at,
            audio_ended_at,
            p_yielding,
            recommended_silence_ms,
            source_event_id,
            trace_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        RETURNING id
        """,
        (
            observation.id,
            "final" if observation.is_final else "partial",
            observation.text,
            observation.is_final,
            observation.stability,
            observation.audio_started_at,
            observation.audio_ended_at,
            observation.p_yielding,
            observation.recommended_silence_ms,
            observation.source_event_id,
            observation.trace_id,
        ),
    )


def insert_saturation_sql(
    result: SemanticSaturationResult,
    *,
    stt_observation_id: UUID | None,
) -> SqlCommand:
    return SqlCommand(
        """
        INSERT INTO v2_semantic_saturation_observations (
            id,
            stt_observation_id,
            saturation,
            source,
            basis_text,
            trace_id
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        RETURNING id
        """,
        (
            result.id,
            stt_observation_id,
            result.saturation,
            result.source,
            result.basis_text,
            result.trace_id,
        ),
    )


def insert_scheduler_decision_sql(
    output: SpeechSchedulerOutput,
    *,
    stt_observation_id: UUID | None,
    semantic_saturation_id: UUID | None,
) -> SqlCommand:
    return SqlCommand(
        """
        INSERT INTO v2_speech_scheduler_decisions (
            id,
            stt_observation_id,
            semantic_saturation_id,
            action,
            text_intent,
            llm_prompt_basis,
            reason,
            score,
            score_breakdown,
            trace_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (id) DO NOTHING
        RETURNING id
        """,
        (
            output.id,
            stt_observation_id,
            semantic_saturation_id,
            output.action.value,
            output.text_intent.value,
            output.llm_prompt_basis,
            output.reason,
            output.score,
            _json_dump(output.score_breakdown),
            output.trace_id,
        ),
    )


def insert_speech_order_sql(order: SpeechOrder) -> SqlCommand:
    return SqlCommand(
        """
        INSERT INTO v2_speech_orders (
            id,
            scheduler_decision_id,
            text,
            mode,
            reason,
            priority,
            supersedes_order_id,
            trace_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        RETURNING id
        """,
        (
            order.id,
            order.scheduler_decision_id,
            order.text,
            order.mode.value,
            order.reason,
            order.priority,
            order.supersedes_order_id,
            order.trace_id,
        ),
    )


def insert_prompt_request_for_order_sql(order: SpeechOrder) -> SqlCommand:
    return SqlCommand(
        """
        INSERT INTO v2_prompt_requests (
            id,
            scope,
            priority,
            cancel_policy,
            prompt_text,
            status,
            trace_id
        )
        VALUES (%s, %s, %s, %s, %s, 'completed', %s)
        ON CONFLICT (id) DO NOTHING
        RETURNING id
        """,
        (
            order.id,
            PromptScope.MAIN.value,
            order.priority,
            CancelPolicy.KEEP_UNTIL_COMPLETE.value,
            order.text,
            order.trace_id,
        ),
    )


def insert_audio_output_event_sql(chunk: AudioChunkOut) -> SqlCommand:
    return SqlCommand(
        """
        INSERT INTO v2_audio_output_events (
            id,
            request_id,
            event_kind,
            content_type,
            byte_length,
            is_final,
            trace_id
        )
        VALUES (%s, %s, 'chunk', %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        RETURNING id
        """,
        (
            chunk.id,
            chunk.request_id,
            chunk.content_type,
            len(chunk.chunk),
            chunk.is_final,
            chunk.trace_id,
        ),
    )


def insert_prompt_request_sql(request: PromptRequest) -> SqlCommand:
    return SqlCommand(
        """
        INSERT INTO v2_prompt_requests (
            id,
            scope,
            priority,
            cancel_policy,
            prompt_text,
            status,
            trace_id
        )
        VALUES (%s, %s, %s, %s, %s, 'completed', %s)
        ON CONFLICT (id) DO NOTHING
        RETURNING id
        """,
        (
            request.id,
            request.scope.value,
            request.priority,
            request.cancel_policy.value,
            request.prompt_text,
            request.trace_id,
        ),
    )


def speech_order_from_row(row: dict[str, Any]) -> SpeechOrder:
    return SpeechOrder(
        id=row["id"],
        scheduler_decision_id=row.get("scheduler_decision_id"),
        text=str(row["text"]),
        mode=SpeechOrderMode(str(row["mode"])),
        reason=str(row["reason"]),
        priority=int(row["priority"]),
        supersedes_order_id=row.get("supersedes_order_id"),
        trace_id=row["trace_id"],
        created_at=row["created_at"],
    )


def notify_speech_order_sql(order_id: UUID) -> tuple[str, dict[str, str]]:
    return notify_sql("v2_speech_order", order_id)


def notify_stt_observation_sql(observation_id: UUID) -> tuple[str, dict[str, str]]:
    return notify_sql("v2_stt_observation", observation_id)


def _json_dump(value: dict[str, float]) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
