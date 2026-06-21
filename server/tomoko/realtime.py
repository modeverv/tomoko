from __future__ import annotations

import os

import psycopg
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from server.llm.chat import StaticChatBackend, create_default_real_chat_backend
from server.shared.db import default_dsn
from server.shared.models import (
    DurableUtterance,
    PartialTranscriptObservation,
    SpeechOrderMode,
    TurnMaterials,
)
from server.tomoko.conversation import TomokoConversationCore, TomokoConversationResult
from server.tomoko.db_bridge import (
    SqlCommand,
    insert_conversation_session_sql,
    insert_prompt_request_sql,
    insert_saturation_sql,
    insert_scheduler_decision_sql,
    insert_speech_order_sql,
    insert_stt_observation_sql,
    insert_utterance_sql,
)
from server.tomoko.main import TomokoProcessCore
from server.tomoko.prompt import PromptBuilderV2
from server.tomoko.scheduler import SpeechScheduler
from server.tomoko.semantic import SemanticSaturationJudge, create_default_saturation_judge
from server.tomoko.session import SessionBoundaryModel
from server.tomoko.turn_state import TurnMaterialState

app = FastAPI(title="Tomoko v2 realtime control")
app.state.turn_material_state = TurnMaterialState()


@app.websocket("/internal/hot-path")
async def hot_path_realtime(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_json({"type": "ready", "process": "tomoko-realtime"})
    state: TurnMaterialState = app.state.turn_material_state
    conversation_core = _conversation_core()
    try:
        while True:
            payload = await websocket.receive_json()
            event_type = payload.get("type")
            if event_type == "turn_materials":
                materials_payload = dict(payload)
                materials_payload.pop("type", None)
                materials = TurnMaterials.from_dict(materials_payload)
                await state.update(materials)
                conversation_core.update_turn_materials(materials)
                _console_event(
                    "turn_materials",
                    p_yielding=materials.p_yielding,
                    speech_probability=materials.speech_probability,
                    silence_ms=materials.silence_ms,
                )
                await websocket.send_json(
                    {
                        "type": "turn_materials_ack",
                        "materials_id": str(materials.id),
                    }
                )
                continue
            if event_type == "stt_observation":
                observation_payload = dict(payload)
                observation_payload.pop("type", None)
                observation = PartialTranscriptObservation.from_dict(observation_payload)
                latest_materials = await state.get_latest()
                if latest_materials is not None:
                    conversation_core.update_turn_materials(latest_materials)
                    if observation.p_yielding is None and latest_materials.p_yielding is not None:
                        observation.p_yielding = latest_materials.p_yielding
                    _console_event(
                        "stt_observation_materials",
                        observation_id=str(observation.id),
                        material_id=str(latest_materials.id),
                        p_yielding=latest_materials.p_yielding,
                        speech_probability=latest_materials.speech_probability,
                        silence_ms=latest_materials.silence_ms,
                    )
                result = await conversation_core.handle_observation(observation)
                await _persist_result_if_enabled(result)
                _console_event(
                    "stt_observation",
                    observation_id=str(observation.id),
                    final=observation.is_final,
                    text=observation.text,
                )
                await websocket.send_json(
                    {
                        "type": "stt_observation_ack",
                        "observation_id": str(observation.id),
                        "action": result.scheduler_output.action.value,
                        "reason": result.scheduler_output.reason,
                        "score": result.scheduler_output.score,
                        "score_breakdown": result.scheduler_output.score_breakdown,
                        "p_yielding": observation.p_yielding,
                    }
                )
                if result.speech_order is not None:
                    if result.speech_order.mode == SpeechOrderMode.STOP:
                        await websocket.send_json(
                            {
                                "type": "cancel_order",
                                "order_id": str(result.speech_order.id),
                                "reason": result.speech_order.reason,
                                "trace_id": str(result.speech_order.trace_id),
                            }
                        )
                    else:
                        await websocket.send_json(
                            {"type": "speech_order", **result.speech_order.to_dict()}
                        )
                continue
            if event_type == "playback_state":
                await websocket.send_json(
                    {
                        "type": "playback_state_ack",
                        "playback_active": bool(payload.get("playback_active", False)),
                    }
                )
                continue
            await websocket.send_json(
                {"type": "error", "reason": "unsupported_event", "event": event_type}
            )
    except WebSocketDisconnect:
        _console_event("ws_disconnected")


async def _persist_result_if_enabled(result: TomokoConversationResult) -> None:
    if os.environ.get("TOMOKO_V2_WS_PERSIST", "1") == "0":
        return
    try:
        async with await psycopg.AsyncConnection.connect(
            os.environ.get("TOMOKO_DATABASE_URL", default_dsn()),
            autocommit=True,
        ) as conn:
            await _persist_result(conn, result)
    except Exception as exc:
        _console_event("persist_failed", error=type(exc).__name__)


async def _persist_result(
    conn: psycopg.AsyncConnection[object],
    result: TomokoConversationResult,
) -> None:
    await _execute(conn, insert_stt_observation_sql(result.observation))
    if result.durable_utterance is not None:
        await _execute(
            conn,
            insert_conversation_session_sql(
                session_id=result.durable_utterance.session_id,
                activity_at=result.durable_utterance.created_at,
                trace_id=result.durable_utterance.trace_id,
            ),
        )
        await _execute(conn, insert_utterance_sql(result.durable_utterance))
    await _execute(
        conn,
        insert_saturation_sql(
            result.saturation,
            stt_observation_id=result.observation.id,
        ),
    )
    await _execute(
        conn,
        insert_scheduler_decision_sql(
            result.scheduler_output,
            stt_observation_id=result.observation.id,
            semantic_saturation_id=result.saturation.id,
        ),
    )
    if result.prompt_request is not None:
        await _execute(conn, insert_prompt_request_sql(result.prompt_request))
    if result.speech_order is not None:
        await _execute(conn, insert_speech_order_sql(result.speech_order))
        if (
            result.speech_order.text
            and result.durable_utterance is not None
        ):
            await _execute(
                conn,
                insert_utterance_sql(
                    DurableUtterance(
                        session_id=result.durable_utterance.session_id,
                        speaker="tomoko",
                        text=result.speech_order.text,
                        trace_id=result.speech_order.trace_id,
                    )
                ),
            )


async def _execute(conn: psycopg.AsyncConnection[object], command: SqlCommand) -> None:
    await conn.execute(command.query, command.params)


def _conversation_core() -> TomokoConversationCore:
    core = getattr(app.state, "conversation_core", None)
    if core is None:
        session_model = SessionBoundaryModel()
        chat_backend = (
            StaticChatBackend([os.environ.get("TOMOKO_V2_FAKE_REPLY", "うん、聞こえてるよ。")])
            if os.environ.get("TOMOKO_V2_FAKE_RUNTIME") == "1"
            else create_default_real_chat_backend()
        )
        core = TomokoConversationCore(
            session_model=session_model,
            saturation_judge=(
                SemanticSaturationJudge()
                if os.environ.get("TOMOKO_V2_FAKE_RUNTIME") == "1"
                else create_default_saturation_judge()
            ),
            scheduler=SpeechScheduler(),
            chat_backend=chat_backend,
            tomoko_core=TomokoProcessCore(session_model),
            prompt_builder=PromptBuilderV2(),
        )
        app.state.conversation_core = core
    return core


def _console_event(event: str, **fields: object) -> None:
    parts = [f"[tomoko:realtime] {event}"]
    for key, value in fields.items():
        text = str(value)
        if len(text) > 120:
            text = text[:117] + "..."
        parts.append(f"{key}={text!r}")
    print(" ".join(parts), flush=True)
