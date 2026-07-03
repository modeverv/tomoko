from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from server.shared.models import (
    AudioChunkOut,
    CancelPolicy,
    CandidateLifecycle,
    CandidateRecord,
    DurableUtterance,
    PartialTranscriptObservation,
    PromptRequest,
    PromptScope,
    SemanticSaturationResult,
    SessionSummary,
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


def insert_conversation_session_sql(
    *,
    session_id: UUID,
    activity_at: datetime,
    trace_id: UUID,
) -> SqlCommand:
    return SqlCommand(
        """
        INSERT INTO v2_conversation_sessions (
            id,
            started_at,
            last_activity_at,
            trace_id
        )
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        RETURNING id
        """,
        (
            session_id,
            activity_at,
            activity_at,
            trace_id,
        ),
    )


def update_conversation_session_activity_sql(
    *,
    session_id: UUID,
    activity_at: datetime,
) -> SqlCommand:
    return SqlCommand(
        """
        UPDATE v2_conversation_sessions
        SET last_activity_at = %s
        WHERE id = %s
        """,
        (
            activity_at,
            session_id,
        ),
    )


def close_conversation_session_sql(
    *,
    session_id: UUID,
    ended_at: datetime,
    reason: str,
) -> SqlCommand:
    return SqlCommand(
        """
        UPDATE v2_conversation_sessions
        SET ended_at = %s,
            close_reason = %s
        WHERE id = %s
        """,
        (
            ended_at,
            reason,
            session_id,
        ),
    )


def insert_utterance_sql(utterance: DurableUtterance) -> SqlCommand:
    return SqlCommand(
        """
        INSERT INTO v2_utterances (
            id,
            session_id,
            stt_observation_id,
            speaker,
            text,
            trace_id
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        RETURNING id
        """,
        (
            utterance.id,
            utterance.session_id,
            utterance.stt_observation_id,
            utterance.speaker,
            utterance.text,
            utterance.trace_id,
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


def select_unsummarized_closed_sessions_sql(*, limit: int = 4) -> SqlCommand:
    return SqlCommand(
        """
        SELECT s.id
        FROM v2_conversation_sessions s
        WHERE s.ended_at IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM v2_session_summaries summary
              WHERE summary.session_id = s.id
          )
        ORDER BY s.ended_at ASC, s.created_at ASC
        LIMIT %s
        """,
        (limit,),
    )


async def load_unsummarized_closed_session_ids(
    conn: Any,
    *,
    limit: int = 4,
) -> list[UUID]:
    command = select_unsummarized_closed_sessions_sql(limit=limit)
    cursor = await conn.execute(command.query, command.params)
    rows = await cursor.fetchall()
    return [row["id"] for row in rows]


def select_session_utterance_texts_sql(session_id: UUID) -> SqlCommand:
    return SqlCommand(
        """
        SELECT text
        FROM v2_utterances
        WHERE session_id = %s
        ORDER BY created_at ASC, id ASC
        """,
        (session_id,),
    )


async def load_session_utterance_texts(conn: Any, session_id: UUID) -> list[str]:
    command = select_session_utterance_texts_sql(session_id)
    cursor = await conn.execute(command.query, command.params)
    rows = await cursor.fetchall()
    return [str(row["text"]) for row in rows]


def insert_session_summary_sql(summary: SessionSummary) -> SqlCommand:
    return SqlCommand(
        """
        INSERT INTO v2_session_summaries (
            id,
            session_id,
            keyword,
            conclusion,
            summary_text,
            trace_id
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        RETURNING id
        """,
        (
            summary.id,
            summary.session_id,
            summary.keyword,
            summary.conclusion,
            _summary_text(summary),
            summary.trace_id,
        ),
    )


def insert_summary_embedding_sql(summary: SessionSummary) -> SqlCommand:
    return SqlCommand(
        """
        INSERT INTO v2_summary_embeddings (
            id,
            summary_id,
            embedding,
            trace_id
        )
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        RETURNING id
        """,
        (
            summary.id,
            summary.id,
            list(summary.embedding),
            summary.trace_id,
        ),
    )


def insert_world_document_sql(
    *,
    document_id: UUID,
    source: str,
    source_key: str,
    raw_text: str,
    metadata: Mapping[str, Any],
    trace_id: UUID,
) -> SqlCommand:
    return SqlCommand(
        """
        INSERT INTO v2_world_documents (
            id,
            source,
            source_key,
            raw_text,
            metadata,
            trace_id
        )
        VALUES (%s, %s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (id) DO UPDATE SET
            raw_text = EXCLUDED.raw_text,
            metadata = EXCLUDED.metadata,
            trace_id = EXCLUDED.trace_id
        RETURNING id
        """,
        (
            document_id,
            source,
            source_key,
            raw_text,
            _json_dump(dict(metadata)),
            trace_id,
        ),
    )


def insert_world_item_sql(
    *,
    item_id: UUID,
    document_id: UUID,
    title: str,
    body: str,
    confidence: float,
    flags: Mapping[str, Any],
    trace_id: UUID,
) -> SqlCommand:
    return SqlCommand(
        """
        INSERT INTO v2_world_items (
            id,
            document_id,
            title,
            body,
            confidence,
            flags,
            trace_id
        )
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (id) DO UPDATE SET
            title = EXCLUDED.title,
            body = EXCLUDED.body,
            confidence = EXCLUDED.confidence,
            flags = EXCLUDED.flags,
            trace_id = EXCLUDED.trace_id
        RETURNING id
        """,
        (
            item_id,
            document_id,
            title,
            body,
            confidence,
            _json_dump(dict(flags)),
            trace_id,
        ),
    )


def insert_world_interpretation_sql(
    *,
    interpretation_id: UUID,
    item_id: UUID,
    summary: str,
    confidence: float,
    flags: Mapping[str, Any],
    trace_id: UUID,
) -> SqlCommand:
    return SqlCommand(
        """
        INSERT INTO v2_world_interpretations (
            id,
            item_id,
            summary,
            confidence,
            flags,
            trace_id
        )
        VALUES (%s, %s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (id) DO UPDATE SET
            summary = EXCLUDED.summary,
            confidence = EXCLUDED.confidence,
            flags = EXCLUDED.flags,
            trace_id = EXCLUDED.trace_id
        RETURNING id
        """,
        (
            interpretation_id,
            item_id,
            summary,
            confidence,
            _json_dump(dict(flags)),
            trace_id,
        ),
    )


def select_recent_session_summaries_sql(*, limit: int = 8) -> SqlCommand:
    return SqlCommand(
        """
        SELECT
            s.id,
            s.session_id,
            s.keyword,
            s.conclusion,
            e.embedding,
            s.trace_id,
            s.created_at
        FROM v2_session_summaries s
        LEFT JOIN v2_summary_embeddings e ON e.summary_id = s.id
        ORDER BY s.created_at DESC
        LIMIT %s
        """,
        (limit,),
    )


def session_summary_from_row(row: dict[str, Any]) -> SessionSummary:
    return SessionSummary(
        id=row["id"],
        session_id=row["session_id"],
        keyword=str(row["keyword"]),
        conclusion=str(row["conclusion"]),
        embedding=tuple(row.get("embedding") or ()),
        trace_id=row["trace_id"],
        created_at=row["created_at"],
    )


async def load_recent_session_summaries(
    conn: Any,
    *,
    limit: int = 8,
) -> list[SessionSummary]:
    command = select_recent_session_summaries_sql(limit=limit)
    cursor = await conn.execute(command.query, command.params)
    rows = await cursor.fetchall()
    return [session_summary_from_row(dict(row)) for row in rows]


def select_recent_world_interpretations_sql(*, limit: int = 8) -> SqlCommand:
    return SqlCommand(
        """
        SELECT
            wi.id,
            wi.summary,
            wi.confidence,
            wi.flags,
            wi.trace_id,
            wi.created_at
        FROM v2_world_interpretations wi
        JOIN v2_world_items item ON item.id = wi.item_id
        JOIN v2_world_documents doc ON doc.id = item.document_id
        ORDER BY wi.created_at DESC
        LIMIT %s
        """,
        (limit,),
    )


async def load_recent_world_interpretation_rows(
    conn: Any,
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    command = select_recent_world_interpretations_sql(limit=limit)
    cursor = await conn.execute(command.query, command.params)
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


def insert_candidate_sql(record: CandidateRecord) -> SqlCommand:
    return SqlCommand(
        """
        INSERT INTO v2_candidates (
            id,
            seed_id,
            source,
            source_key,
            text,
            priority,
            urgency,
            intrusion,
            maturity,
            candidate_score,
            lifecycle,
            context_tags,
            expires_at,
            spoken_at,
            trace_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source, source_key) DO UPDATE SET
            seed_id = EXCLUDED.seed_id,
            text = EXCLUDED.text,
            priority = EXCLUDED.priority,
            urgency = EXCLUDED.urgency,
            intrusion = EXCLUDED.intrusion,
            maturity = EXCLUDED.maturity,
            candidate_score = EXCLUDED.candidate_score,
            lifecycle = EXCLUDED.lifecycle,
            context_tags = EXCLUDED.context_tags,
            expires_at = EXCLUDED.expires_at,
            spoken_at = EXCLUDED.spoken_at,
            trace_id = EXCLUDED.trace_id
        RETURNING id
        """,
        (
            record.id,
            record.seed_id,
            record.source,
            record.source_key,
            record.text,
            record.priority,
            record.urgency,
            record.intrusion,
            record.maturity,
            record.candidate_score,
            record.lifecycle.value,
            list(record.context_tags),
            record.expires_at,
            record.spoken_at,
            record.trace_id,
        ),
    )


def select_active_candidates_sql(*, limit: int = 8) -> SqlCommand:
    return SqlCommand(
        """
        SELECT
            id,
            seed_id,
            source,
            source_key,
            text,
            priority,
            urgency,
            intrusion,
            maturity,
            candidate_score,
            lifecycle,
            context_tags,
            expires_at,
            spoken_at,
            trace_id,
            created_at
        FROM v2_candidates
        WHERE lifecycle = %s
          AND (expires_at IS NULL OR expires_at > now())
        ORDER BY candidate_score DESC, urgency DESC, priority DESC, created_at DESC
        LIMIT %s
        """,
        (CandidateLifecycle.ACTIVE.value, limit),
    )


def candidate_from_row(row: dict[str, Any]) -> CandidateRecord:
    return CandidateRecord(
        id=row["id"],
        seed_id=row["seed_id"],
        source=str(row["source"]),
        source_key=str(row["source_key"]),
        text=str(row["text"]),
        priority=float(row["priority"]),
        urgency=float(row["urgency"]),
        intrusion=float(row["intrusion"]),
        maturity=float(row["maturity"]),
        lifecycle=CandidateLifecycle(str(row["lifecycle"])),
        context_tags=tuple(row["context_tags"]),
        candidate_score=float(row["candidate_score"]),
        expires_at=row.get("expires_at"),
        spoken_at=row.get("spoken_at"),
        trace_id=row["trace_id"],
        created_at=row["created_at"],
    )


async def load_active_candidates(conn: Any, *, limit: int = 8) -> list[CandidateRecord]:
    command = select_active_candidates_sql(limit=limit)
    cursor = await conn.execute(command.query, command.params)
    rows = await cursor.fetchall()
    return [candidate_from_row(dict(row)) for row in rows]


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


def _summary_text(summary: SessionSummary) -> str:
    return f"{summary.keyword}: {summary.conclusion}"
