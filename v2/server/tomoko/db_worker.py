from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from server.llm.chat import ChatBackend, StaticChatBackend, create_default_real_chat_backend
from server.shared.logging import JsonlLogger
from server.shared.models import (
    ConversationHistoryItem,
    DurableUtterance,
    PartialTranscriptObservation,
    new_id,
)
from server.shared.notify import parse_id_payload
from server.tomoko.append_dedupe import create_default_append_dedupe_guard
from server.tomoko.conversation import TomokoConversationCore, TomokoConversationResult
from server.tomoko.db_bridge import (
    SqlCommand,
    close_conversation_session_sql,
    insert_conversation_session_sql,
    insert_prompt_request_sql,
    insert_saturation_sql,
    insert_scheduler_decision_sql,
    insert_speech_order_sql,
    insert_utterance_sql,
    notify_speech_order_sql,
    update_conversation_session_activity_sql,
)
from server.tomoko.gates import LlmFireGate, SpeechEmissionGate
from server.tomoko.main import TomokoProcessCore
from server.tomoko.scheduler import SpeechScheduler
from server.tomoko.semantic import SemanticSaturationJudge
from server.tomoko.session import SessionBoundaryModel


@dataclass(slots=True)
class TomokoDbWorker:
    dsn: str
    conversation_core: TomokoConversationCore
    logger: JsonlLogger

    async def run_forever(self) -> None:
        async with await self._open_listener() as listener:
            async with await self._open_connections() as conn:
                _console_event("listen_start", channel="v2_stt_observation")
                async for notify in listener.notifies():
                    observation_id = parse_id_payload(notify.payload)
                    await self.process_observation_id(observation_id, conn)

    async def process_observation_id(
        self,
        observation_id: UUID,
        conn: psycopg.AsyncConnection[dict[str, object]],
    ) -> TomokoConversationResult | None:
        observation = await load_stt_observation(conn, observation_id)
        if observation is None:
            _console_event("stt_observation_missing", observation_id=str(observation_id))
            return None
        _console_event(
            "stt_observation_loaded",
            observation_id=str(observation.id),
            final=observation.is_final,
            text=observation.text,
        )
        session_id: UUID | None = None
        prior_session_history: list[ConversationHistoryItem] | None = None
        if await should_assign_session(self.conversation_core, observation):
            session_id = await assign_conversation_session(
                conn,
                observation,
                idle_gap_to_new_session_ms=(
                    self.conversation_core.session_model.idle_gap_to_new_session_ms
                ),
            )
            prior_session_history = await load_session_history(conn, session_id)

        result = await self.conversation_core.handle_observation(
            observation,
            session_id_override=session_id,
            prior_session_history=prior_session_history,
        )
        if result.durable_utterance is not None and session_id is not None:
            await execute_command(conn, insert_utterance_sql(result.durable_utterance))
        await execute_command(
            conn,
            insert_saturation_sql(result.saturation, stt_observation_id=observation.id),
        )
        await execute_command(
            conn,
            insert_scheduler_decision_sql(
                result.scheduler_output,
                stt_observation_id=observation.id,
                semantic_saturation_id=result.saturation.id,
            ),
        )
        if result.prompt_request is not None:
            await execute_command(conn, insert_prompt_request_sql(result.prompt_request))
        if result.speech_order is not None:
            await execute_command(conn, insert_speech_order_sql(result.speech_order))
            if result.speech_order.text and session_id is not None:
                await execute_command(
                    conn,
                    insert_utterance_sql(
                        DurableUtterance(
                            session_id=session_id,
                            speaker="tomoko",
                            text=result.speech_order.text,
                            trace_id=result.speech_order.trace_id,
                        )
                    ),
                )
            query, params = notify_speech_order_sql(result.speech_order.id)
            await conn.execute(query, params)
            _console_event(
                "speech_order_notified",
                order_id=str(result.speech_order.id),
                mode=result.speech_order.mode.value,
            )
        self.logger.log(
            "tomoko_db_worker_processed",
            observation_id=str(observation.id),
            speech_order_id=str(result.speech_order.id) if result.speech_order else None,
            action=result.scheduler_output.action.value,
            score=result.scheduler_output.score,
        )
        return result

    async def _open_listener(self) -> psycopg.AsyncConnection[object]:
        listener = await psycopg.AsyncConnection.connect(
            self.dsn,
            autocommit=True,
        )
        await listener.execute("LISTEN v2_stt_observation")
        return listener

    async def _open_connections(self) -> psycopg.AsyncConnection[dict[str, object]]:
        return await psycopg.AsyncConnection.connect(
            self.dsn,
            autocommit=True,
            row_factory=dict_row,
        )


async def load_stt_observation(
    conn: psycopg.AsyncConnection[dict[str, object]],
    observation_id: UUID,
) -> PartialTranscriptObservation | None:
    cursor = await conn.execute(
        """
        SELECT
            id,
            text,
            is_final,
            stability,
            audio_started_at,
            audio_ended_at,
            p_yielding,
            recommended_silence_ms,
            source_event_id,
            trace_id,
            created_at
        FROM v2_stt_observations
        WHERE id = %s
        """,
        (observation_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return PartialTranscriptObservation(
        id=row["id"],
        text=str(row["text"]),
        is_final=bool(row["is_final"]),
        stability=float(row["stability"]),
        audio_started_at=row["audio_started_at"],
        audio_ended_at=row["audio_ended_at"],
        p_yielding=row["p_yielding"],
        recommended_silence_ms=row["recommended_silence_ms"],
        source_event_id=row["source_event_id"],
        trace_id=row["trace_id"],
        created_at=row["created_at"],
    )


async def should_assign_session(
    conversation_core: TomokoConversationCore,
    observation: PartialTranscriptObservation,
) -> bool:
    if not observation.is_final:
        return False
    core = conversation_core.tomoko_core or TomokoProcessCore(conversation_core.session_model)
    return core.block_reason_for_final_observation(observation) is None


async def assign_conversation_session(
    conn: psycopg.AsyncConnection[dict[str, object]],
    observation: PartialTranscriptObservation,
    *,
    idle_gap_to_new_session_ms: int,
) -> UUID:
    activity_at = observation.audio_ended_at
    cursor = await conn.execute(
        """
        SELECT id, last_activity_at
        FROM v2_conversation_sessions
        WHERE ended_at IS NULL
        ORDER BY last_activity_at DESC, created_at DESC
        LIMIT 1
        """
    )
    row = await cursor.fetchone()
    if row is None:
        session_id = new_id()
        await execute_command(
            conn,
            insert_conversation_session_sql(
                session_id=session_id,
                activity_at=activity_at,
                trace_id=observation.trace_id,
            ),
        )
        _console_event("session_created", session_id=str(session_id), reason="no_open")
        return session_id

    session_id = row["id"]
    last_activity_at = row["last_activity_at"]
    gap_ms = (activity_at - last_activity_at).total_seconds() * 1000
    if gap_ms >= idle_gap_to_new_session_ms:
        await execute_command(
            conn,
            close_conversation_session_sql(
                session_id=session_id,
                ended_at=activity_at,
                reason="idle_gap",
            ),
        )
        new_session_id = new_id()
        await execute_command(
            conn,
            insert_conversation_session_sql(
                session_id=new_session_id,
                activity_at=activity_at,
                trace_id=observation.trace_id,
            ),
        )
        _console_event(
            "session_created",
            session_id=str(new_session_id),
            reason="idle_gap",
            closed_session_id=str(session_id),
            gap_ms=round(gap_ms, 1),
        )
        return new_session_id

    await execute_command(
        conn,
        update_conversation_session_activity_sql(
            session_id=session_id,
            activity_at=activity_at,
        ),
    )
    _console_event(
        "session_reused",
        session_id=str(session_id),
        gap_ms=round(gap_ms, 1),
    )
    return session_id


async def load_session_history(
    conn: psycopg.AsyncConnection[dict[str, object]],
    session_id: UUID,
) -> list[ConversationHistoryItem]:
    cursor = await conn.execute(
        """
        SELECT speaker, text
        FROM v2_utterances
        WHERE session_id = %s
        ORDER BY created_at ASC, id ASC
        """,
        (session_id,),
    )
    rows = await cursor.fetchall()
    return [
        ConversationHistoryItem(speaker=str(row["speaker"]), text=str(row["text"]))
        for row in rows
    ]


async def execute_command(
    conn: psycopg.AsyncConnection[dict[str, object]],
    command: SqlCommand,
) -> None:
    await conn.execute(command.query, command.params)


def create_default_worker(
    dsn: str,
    *,
    fake_reply: str | None = None,
    logger_path: Path = Path("logs/v2-runtime.jsonl"),
) -> TomokoDbWorker:
    chat_backend: ChatBackend = StaticChatBackend(
        [fake_reply]
    ) if fake_reply is not None else create_default_real_chat_backend()
    logger = JsonlLogger(logger_path)
    return TomokoDbWorker(
        dsn=dsn,
        conversation_core=TomokoConversationCore(
            session_model=SessionBoundaryModel(),
            saturation_judge=SemanticSaturationJudge(logger=logger),
            scheduler=SpeechScheduler(logger=logger),
            llm_fire_gate=LlmFireGate(logger=logger),
            speech_emission_gate=SpeechEmissionGate(logger=logger),
            append_dedupe_guard=create_default_append_dedupe_guard(),
            chat_backend=chat_backend,
        ),
        logger=logger,
    )


async def run_default_worker(dsn: str, *, fake_reply: str | None = None) -> None:
    await create_default_worker(dsn, fake_reply=fake_reply).run_forever()


def _console_event(event: str, **fields: object) -> None:
    parts = [f"[tomoko:tomoko-db] {event}"]
    for key, value in fields.items():
        text = str(value)
        if len(text) > 120:
            text = text[:117] + "..."
        parts.append(f"{key}={text!r}")
    print(" ".join(parts), flush=True)
