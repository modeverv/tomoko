from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from time import monotonic
from uuid import uuid4

import psycopg
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from psycopg.rows import dict_row

from server.llm.chat import StaticChatBackend, create_default_real_chat_backend
from server.shared.db import default_dsn
from server.shared.models import (
    CandidateLifecycle,
    CandidateRecord,
    DurableUtterance,
    PartialTranscriptObservation,
    PersonalityMaterials,
    SpeechOrderMode,
    TurnMaterials,
    UserStatusObservation,
)
from server.think.main import build_candidates, world_info_seed
from server.tomoko.calendar import fake_calendar_provider_from_env_payload
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
    load_active_candidates,
)
from server.tomoko.main import TomokoProcessCore
from server.tomoko.prompt import PromptBuilderV2
from server.tomoko.scheduler import SpeechScheduler
from server.tomoko.semantic import SemanticSaturationJudge, create_default_saturation_judge
from server.tomoko.session import SessionBoundaryModel
from server.tomoko.turn_state import TurnMaterialState

app = FastAPI(title="Tomoko v2 realtime control")
app.state.turn_material_state = TurnMaterialState()


@dataclass(slots=True)
class _DbCandidateCache:
    dsn: str
    limit: int = 8
    ttl_sec: float = 1.0
    _loaded_at: float = 0.0
    _records: list[CandidateRecord] = field(default_factory=list)

    async def active_candidates(self) -> list[CandidateRecord]:
        now = monotonic()
        if self._loaded_at > 0 and now - self._loaded_at <= self.ttl_sec:
            return list(self._records)
        async with await psycopg.AsyncConnection.connect(
            self.dsn,
            autocommit=True,
            row_factory=dict_row,
        ) as conn:
            self._records = await load_active_candidates(conn, limit=self.limit)
        self._loaded_at = now
        return list(self._records)


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
                await _refresh_db_candidates_if_enabled(conversation_core)
                result = await conversation_core.handle_observation(observation)
                await _persist_result_if_enabled(result)
                _console_event(
                    "stt_observation",
                    observation_id=str(observation.id),
                    final=observation.is_final,
                    text=observation.text,
                )
                order_count = 0
                if result.speech_order is not None:
                    order_count = 1
                    if result.speech_order.mode != SpeechOrderMode.STOP:
                        order_count += len(result.followup_orders)
                await websocket.send_json(
                    {
                        "type": "stt_observation_ack",
                        "observation_id": str(observation.id),
                        "action": result.scheduler_output.action.value,
                        "reason": result.scheduler_output.reason,
                        "score": result.scheduler_output.score,
                        "score_breakdown": result.scheduler_output.score_breakdown,
                        "p_yielding": observation.p_yielding,
                        "order_count": order_count,
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
                        for followup in result.followup_orders:
                            await websocket.send_json(
                                {"type": "speech_order", **followup.to_dict()}
                            )
                continue
            if event_type == "user_status":
                status_payload = dict(payload)
                status_payload.pop("type", None)
                observation = UserStatusObservation.from_dict(status_payload)
                conversation_core.update_user_status(observation)
                await websocket.send_json(
                    {
                        "type": "user_status_ack",
                        "user_status_id": str(observation.id),
                        "present": observation.present,
                    }
                )
                _console_event(
                    "user_status",
                    user_status_id=str(observation.id),
                    present=observation.present,
                    activity=observation.activity_label,
                )
                continue
            if event_type == "scenario_fixture":
                calendar_items = 0
                fake_calendar = payload.get("fake_calendar")
                if isinstance(fake_calendar, list):
                    provider = fake_calendar_provider_from_env_payload(fake_calendar)
                    conversation_core.update_calendar_items_provider(provider)
                    calendar_items = len(provider())
                await websocket.send_json(
                    {
                        "type": "scenario_fixture_ack",
                        "calendar_items": calendar_items,
                    }
                )
                _console_event(
                    "scenario_fixture",
                    calendar_items=calendar_items,
                )
                continue
            if event_type == "initiative_tick":
                latest_materials = await state.get_latest()
                if latest_materials is not None:
                    conversation_core.update_turn_materials(latest_materials)
                await _refresh_db_candidates_if_enabled(conversation_core)
                result = await conversation_core.handle_initiative_tick()
                await _persist_result_if_enabled(result)
                order_count = 0
                if result.speech_order is not None:
                    order_count = 1
                    if result.speech_order.mode != SpeechOrderMode.STOP:
                        order_count += len(result.followup_orders)
                await websocket.send_json(
                    {
                        "type": "initiative_tick_ack",
                        "action": result.scheduler_output.action.value,
                        "text_intent": result.scheduler_output.text_intent.value,
                        "basis_text": result.scheduler_output.llm_prompt_basis,
                        "reason": result.scheduler_output.reason,
                        "score": result.scheduler_output.score,
                        "score_breakdown": result.scheduler_output.score_breakdown,
                        "order_count": order_count,
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
                        for followup in result.followup_orders:
                            await websocket.send_json(
                                {"type": "speech_order", **followup.to_dict()}
                            )
                _console_event(
                    "initiative_tick",
                    action=result.scheduler_output.action.value,
                    reason=result.scheduler_output.reason,
                    order_count=order_count,
                )
                continue
            if event_type == "playback_state":
                playback_active = bool(payload.get("playback_active", False))
                conversation_core.update_playback_state(playback_active)
                await websocket.send_json(
                    {
                        "type": "playback_state_ack",
                        "playback_active": playback_active,
                    }
                )
                continue
            if event_type == "reset_conversation":
                state = TurnMaterialState()
                app.state.turn_material_state = state
                conversation_core = _reset_conversation_core()
                await websocket.send_json({"type": "reset_conversation_ack"})
                _console_event("reset_conversation")
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
    for followup in result.followup_orders:
        await _execute(conn, insert_speech_order_sql(followup))
        if followup.text and result.durable_utterance is not None:
            await _execute(
                conn,
                insert_utterance_sql(
                    DurableUtterance(
                        session_id=result.durable_utterance.session_id,
                        speaker="tomoko",
                        text=followup.text,
                        trace_id=followup.trace_id,
                    )
                ),
            )


async def _execute(conn: psycopg.AsyncConnection[object], command: SqlCommand) -> None:
    await conn.execute(command.query, command.params)


async def _refresh_db_candidates_if_enabled(core: TomokoConversationCore) -> None:
    cache = _db_candidate_cache()
    if cache is None:
        return
    try:
        core.update_candidate_records(await cache.active_candidates())
    except Exception as exc:
        _console_event("candidate_refresh_failed", error=type(exc).__name__)


def _db_candidate_cache() -> _DbCandidateCache | None:
    if not _db_candidates_enabled():
        return None
    dsn = os.environ.get("TOMOKO_DATABASE_URL", default_dsn())
    limit = int(os.environ.get("TOMOKO_V2_DB_CANDIDATE_LIMIT", "8"))
    ttl_sec = float(os.environ.get("TOMOKO_V2_DB_CANDIDATE_TTL_SEC", "1.0"))
    cache = getattr(app.state, "db_candidate_cache", None)
    if (
        cache is None
        or cache.dsn != dsn
        or cache.limit != limit
        or cache.ttl_sec != ttl_sec
    ):
        cache = _DbCandidateCache(dsn=dsn, limit=limit, ttl_sec=ttl_sec)
        app.state.db_candidate_cache = cache
    return cache


def _db_candidates_enabled() -> bool:
    return os.environ.get("TOMOKO_V2_DB_CANDIDATES", "0") != "0"


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
            calendar_items_provider=_fake_calendar_provider(),
            personality_materials=(
                _fake_personality_materials() or PersonalityMaterials()
            ),
            candidate_provider=_fake_candidate_provider(),
        )
        fake_user_status = _fake_user_status_observation()
        if fake_user_status is not None:
            core.update_user_status(fake_user_status)
        app.state.conversation_core = core
    return core


def _reset_conversation_core() -> TomokoConversationCore:
    if hasattr(app.state, "conversation_core"):
        delattr(app.state, "conversation_core")
    return _conversation_core()


def _fake_calendar_provider():
    raw = os.environ.get("TOMOKO_V2_FAKE_CALENDAR")
    if not raw:
        return None
    return fake_calendar_provider_from_env_payload(json.loads(raw))


def _fake_personality_materials() -> PersonalityMaterials | None:
    raw = os.environ.get("TOMOKO_V2_FAKE_PERSONALITY")
    if not raw:
        return None
    payload = json.loads(raw)
    return PersonalityMaterials(
        talkativeness=float(payload.get("talkativeness", 0.5)),
        curiosity=float(payload.get("curiosity", 0.5)),
        restraint=float(payload.get("restraint", 0.5)),
        empathy=float(payload.get("empathy", 0.5)),
        interrupt_tolerance=float(payload.get("interrupt_tolerance", 0.2)),
    )


def _fake_candidate_provider():
    raw_candidates = os.environ.get("TOMOKO_V2_FAKE_CANDIDATES")
    raw_calendar = os.environ.get("TOMOKO_V2_FAKE_CALENDAR")
    raw_world = os.environ.get("TOMOKO_V2_FAKE_WORLD_INFO")
    if not raw_candidates and not raw_calendar and not raw_world:
        return None
    payload = list(json.loads(raw_candidates)) if raw_candidates else []
    calendar_provider = (
        fake_calendar_provider_from_env_payload(json.loads(raw_calendar))
        if raw_calendar
        else None
    )
    world_payload = list(json.loads(raw_world)) if raw_world else []

    def provider() -> list[CandidateRecord]:
        explicit_records = [
            CandidateRecord(
                seed_id=uuid4(),
                source=str(item.get("source", "world")),
                source_key=str(item.get("source_key", item.get("key", "fake"))),
                text=str(item.get("text", "")),
                priority=float(item.get("priority", 0.5)),
                urgency=float(item.get("urgency", 0.0)),
                intrusion=float(item.get("intrusion", 0.0)),
                maturity=float(item.get("maturity", 1.0)),
                lifecycle=CandidateLifecycle(str(item.get("lifecycle", "active"))),
                context_tags=tuple(item.get("context_tags", ())),
                candidate_score=float(item.get("candidate_score", 0.0)),
            )
            for item in payload
        ]
        generated_records = build_candidates(
            calendar_items=calendar_provider() if calendar_provider is not None else {},
            world_seeds=[
                world_info_seed(
                    source_key=str(item.get("source_key", item.get("key", "fake-world"))),
                    text=str(item.get("text", "")),
                    confidence=float(item.get("confidence", 0.0)),
                    stale=bool(item.get("stale", False)),
                    sensitive=bool(item.get("sensitive", False)),
                    private=bool(item.get("private", False)),
                    do_not_speak=bool(item.get("do_not_speak", False)),
                )
                for item in world_payload
            ],
        )
        return [*explicit_records, *generated_records]

    return provider


def _fake_user_status_observation() -> UserStatusObservation | None:
    raw = os.environ.get("TOMOKO_V2_FAKE_USER_STATUS")
    if not raw:
        return None
    payload = json.loads(raw)
    return UserStatusObservation(
        present=bool(payload.get("present", True)),
        activity_label=str(payload.get("activity_label", "unknown_activity")),
        summary=str(payload.get("summary", "")),
        source="fake_env",
        confidence=float(payload.get("confidence", 0.0)),
        visible_text=str(payload.get("visible_text", "")),
        app_name=payload.get("app_name"),
        window_title=payload.get("window_title"),
        url=payload.get("url"),
        artifact_path=payload.get("artifact_path"),
    )


def _console_event(event: str, **fields: object) -> None:
    parts = [f"[tomoko:realtime] {event}"]
    for key, value in fields.items():
        text = str(value)
        if len(text) > 120:
            text = text[:117] + "..."
        parts.append(f"{key}={text!r}")
    print(" ".join(parts), flush=True)
