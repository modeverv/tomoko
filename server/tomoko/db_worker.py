from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from server.llm.chat import ChatBackend, StaticChatBackend, create_default_real_chat_backend
from server.shared.logging import JsonlLogger
from server.shared.models import PartialTranscriptObservation
from server.shared.notify import parse_id_payload
from server.tomoko.conversation import TomokoConversationCore, TomokoConversationResult
from server.tomoko.db_bridge import (
    SqlCommand,
    insert_prompt_request_sql,
    insert_saturation_sql,
    insert_scheduler_decision_sql,
    insert_speech_order_sql,
    notify_speech_order_sql,
)
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
        result = await self.conversation_core.handle_observation(observation)
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
