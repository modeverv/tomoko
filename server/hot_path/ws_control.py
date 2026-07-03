from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, TypeVar
from uuid import UUID

import websockets

from server.hot_path.speech_executor import prompt_request_for_order
from server.shared.models import (
    ModelOutputEvent,
    PartialTranscriptObservation,
    SemanticSaturationResult,
    SpeechOrder,
    SpeechOrderMode,
    SpeechSchedulerAction,
    SpeechSchedulerOutput,
    SpeechTextIntent,
    TurnMaterials,
    utc_now,
)
from server.tomoko.conversation import TomokoConversationResult

T = TypeVar("T")


def stop_order_from_cancel_event(
    event: dict[str, Any],
    *,
    trace_id: UUID,
) -> SpeechOrder:
    order = SpeechOrder(
        text="",
        mode=SpeechOrderMode.STOP,
        reason=str(event.get("reason", "cancel_order")),
        priority=100,
        trace_id=trace_id,
    )
    raw_order_id = event.get("order_id")
    if raw_order_id:
        order.id = UUID(str(raw_order_id))
    return order


@dataclass(slots=True)
class RemoteTomokoWsCore:
    url: str = field(default_factory=lambda: os.environ.get("TOMOKO_INTERNAL_WS_URL", ""))
    request_timeout_sec: float = 30.0
    _ws: Any | None = field(default=None, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _latest_materials: TurnMaterials | None = None

    def update_turn_materials(self, materials: TurnMaterials) -> None:
        self._latest_materials = materials
        _console_event(
            "turn_materials_cached",
            materials_id=materials.id,
            p_yielding=materials.p_yielding,
            speech_probability=round(materials.speech_probability, 4),
            silence_ms=materials.silence_ms,
        )

    async def reset_conversation(self) -> None:
        if not self.url:
            raise RuntimeError("TOMOKO_INTERNAL_WS_URL is required for WS split")

        async def reset(ws: Any) -> None:
            await ws.send(json.dumps({"type": "reset_conversation"}, ensure_ascii=False))
            await self._receive_expected(ws, "reset_conversation_ack")

        async with self._lock:
            await self._with_reconnect(reset)
            self._latest_materials = None
            _console_event("reset_conversation")

    async def set_scenario_fixture(
        self,
        *,
        fake_calendar: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not self.url:
            raise RuntimeError("TOMOKO_INTERNAL_WS_URL is required for WS split")

        async def send_fixture(ws: Any) -> dict[str, Any]:
            payload: dict[str, Any] = {"type": "scenario_fixture"}
            if fake_calendar is not None:
                payload["fake_calendar"] = fake_calendar
            await ws.send(json.dumps(payload, ensure_ascii=False))
            return await self._receive_expected(ws, "scenario_fixture_ack")

        async with self._lock:
            ack = await self._with_reconnect(send_fixture)
            _console_event("scenario_fixture", **ack)
            return ack

    async def handle_observation(
        self,
        observation: PartialTranscriptObservation,
        *,
        session_id_override: object | None = None,
        prior_session_history: object | None = None,
    ) -> TomokoConversationResult:
        del session_id_override, prior_session_history
        if not self.url:
            raise RuntimeError("TOMOKO_INTERNAL_WS_URL is required for WS split")
        async with self._lock:
            return await self._with_reconnect(
                lambda ws: self._handle_observation_ws(ws, observation)
            )

    async def _handle_observation_ws(
        self,
        ws: Any,
        observation: PartialTranscriptObservation,
    ) -> TomokoConversationResult:
        await self._send_latest_materials(ws)
        await ws.send(
            json.dumps(
                {"type": "stt_observation", **observation.to_dict()},
                ensure_ascii=False,
            )
        )
        ack = await self._receive_expected(ws, "stt_observation_ack")
        action = SpeechSchedulerAction(str(ack.get("action", "suppress")))
        expected_orders = ack.get("order_count")
        orders: list[SpeechOrder] = []
        while True:
            if expected_orders is not None and len(orders) >= int(expected_orders):
                break
            if expected_orders is None and orders:
                break
            try:
                message = await asyncio.wait_for(
                    ws.recv(),
                    timeout=(
                        self.request_timeout_sec
                        if expected_orders is not None
                        else 0.05
                    ),
                )
            except TimeoutError:
                break
            event = json.loads(message)
            event_type = event.get("type")
            if event_type == "speech_order":
                payload = dict(event)
                payload.pop("type", None)
                orders.append(SpeechOrder.from_dict(payload))
                continue
            if event_type == "cancel_order":
                orders.append(
                    stop_order_from_cancel_event(
                        event,
                        trace_id=observation.trace_id,
                    )
                )
                continue
            if event_type == "error":
                raise RuntimeError(str(event))
        order = orders[0] if orders else None
        followup_orders = orders[1:]
        if order is not None:
            action = _action_for_order(order)
        scheduler_output = SpeechSchedulerOutput(
            action=action,
            text_intent=(
                SpeechTextIntent.STOP
                if action == SpeechSchedulerAction.STOP
                else SpeechTextIntent.REPLY
            ),
            llm_prompt_basis=observation.text,
            reason=str(ack.get("reason", "remote tomoko ws decision")),
            score=float(ack.get("score", 1.0 if order is not None else 0.0)),
            score_breakdown=dict(ack.get("score_breakdown", {})),
            trace_id=observation.trace_id,
        )
        model_events = [
            ModelOutputEvent(
                request_id=order.id,
                event_kind="complete",
                text=order.text,
                trace_id=order.trace_id,
            )
        ] if order is not None and order.text else []
        return TomokoConversationResult(
            observation=observation,
            durable_utterance=None,
            saturation=SemanticSaturationResult(
                saturation=1.0 if observation.is_final else 0.0,
                source="remote_ws",
                basis_text=observation.text,
                trace_id=observation.trace_id,
            ),
            scheduler_output=scheduler_output,
            context_snapshot=None,
            prompt_request=prompt_request_for_order(order) if order is not None else None,
            speech_order=order,
            model_events=model_events,
            followup_orders=followup_orders,
        )

    async def handle_initiative_tick(self) -> TomokoConversationResult:
        if not self.url:
            raise RuntimeError("TOMOKO_INTERNAL_WS_URL is required for WS split")
        async with self._lock:
            return await self._with_reconnect(self._handle_initiative_tick_ws)

    async def _handle_initiative_tick_ws(self, ws: Any) -> TomokoConversationResult:
        await self._send_latest_materials(ws)
        now = utc_now()
        observation = PartialTranscriptObservation(
            text="",
            is_final=False,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
        await ws.send(json.dumps({"type": "initiative_tick"}, ensure_ascii=False))
        ack = await self._receive_expected(ws, "initiative_tick_ack")
        action = SpeechSchedulerAction(str(ack.get("action", "suppress")))
        expected_orders = ack.get("order_count")
        orders: list[SpeechOrder] = []
        while True:
            if expected_orders is not None and len(orders) >= int(expected_orders):
                break
            if expected_orders is None and orders:
                break
            try:
                message = await asyncio.wait_for(
                    ws.recv(),
                    timeout=(
                        self.request_timeout_sec
                        if expected_orders is not None
                        else 0.05
                    ),
                )
            except TimeoutError:
                break
            event = json.loads(message)
            event_type = event.get("type")
            if event_type == "speech_order":
                payload = dict(event)
                payload.pop("type", None)
                orders.append(SpeechOrder.from_dict(payload))
                continue
            if event_type == "cancel_order":
                orders.append(
                    stop_order_from_cancel_event(
                        event,
                        trace_id=observation.trace_id,
                    )
                )
                continue
            if event_type == "error":
                raise RuntimeError(str(event))
        order = orders[0] if orders else None
        followup_orders = orders[1:]
        if order is not None:
            action = _action_for_order(order)
        text_intent = SpeechTextIntent(str(ack.get("text_intent", "initiative")))
        scheduler_output = SpeechSchedulerOutput(
            action=action,
            text_intent=text_intent,
            llm_prompt_basis=str(ack.get("basis_text", "initiative_tick")),
            reason=str(ack.get("reason", "remote tomoko initiative tick")),
            score=float(ack.get("score", 1.0 if order is not None else 0.0)),
            score_breakdown=dict(ack.get("score_breakdown", {})),
            trace_id=observation.trace_id,
        )
        model_events = [
            ModelOutputEvent(
                request_id=order.id,
                event_kind="complete",
                text=order.text,
                trace_id=order.trace_id,
            )
        ] if order is not None and order.text else []
        return TomokoConversationResult(
            observation=observation,
            durable_utterance=None,
            saturation=SemanticSaturationResult(
                saturation=0.0,
                source="remote_ws_initiative_tick",
                basis_text="",
                trace_id=observation.trace_id,
            ),
            scheduler_output=scheduler_output,
            context_snapshot=None,
            prompt_request=prompt_request_for_order(order) if order is not None else None,
            speech_order=order,
            model_events=model_events,
            followup_orders=followup_orders,
        )

    async def _send_latest_materials(self, ws: Any) -> None:
        if self._latest_materials is None:
            return
        _console_event(
            "turn_materials_send",
            materials_id=self._latest_materials.id,
            p_yielding=self._latest_materials.p_yielding,
            speech_probability=round(self._latest_materials.speech_probability, 4),
            silence_ms=self._latest_materials.silence_ms,
        )
        await ws.send(
            json.dumps(
                {"type": "turn_materials", **self._latest_materials.to_dict()},
                ensure_ascii=False,
            )
        )
        materials_ack = await self._receive_expected(ws, "turn_materials_ack")
        _console_event("turn_materials_ack", **materials_ack)
        await ws.send(
            json.dumps(
                {
                    "type": "playback_state",
                    "playback_active": self._latest_materials.playback_active,
                },
                ensure_ascii=False,
            )
        )
        await self._receive_expected(ws, "playback_state_ack")

    async def aclose(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def _ensure_ws(self) -> Any:
        if self._ws is None:
            _console_event("ws_connecting", url=self.url)
            self._ws = await websockets.connect(self.url)
            ready = await self._receive_expected(self._ws, "ready")
            _console_event("ws_connected", **ready)
        return self._ws

    async def _with_reconnect(self, operation: Callable[[Any], Awaitable[T]]) -> T:
        for attempt in range(2):
            ws = await self._ensure_ws()
            try:
                return await operation(ws)
            except websockets.ConnectionClosed:
                await self._drop_ws()
                if attempt == 1:
                    raise
                _console_event("ws_reconnecting", reason="connection_closed")
        raise RuntimeError("unreachable reconnect state")

    async def _drop_ws(self) -> None:
        ws = self._ws
        self._ws = None
        if ws is not None:
            with suppress(Exception):
                await ws.close()

    async def _receive_expected(self, ws: Any, event_type: str) -> dict[str, Any]:
        message = await asyncio.wait_for(ws.recv(), timeout=self.request_timeout_sec)
        payload = json.loads(message)
        if payload.get("type") != event_type:
            raise RuntimeError(f"expected {event_type}, got {payload}")
        return payload


def create_remote_ws_conversation_core(url: str | None = None) -> RemoteTomokoWsCore:
    return RemoteTomokoWsCore(url=url or os.environ.get("TOMOKO_INTERNAL_WS_URL", ""))


def _action_for_order(order: SpeechOrder) -> SpeechSchedulerAction:
    if order.mode.value == "stop":
        return SpeechSchedulerAction.STOP
    if order.mode.value == "append_after_current":
        return SpeechSchedulerAction.APPEND_AFTER_CURRENT
    return SpeechSchedulerAction.REPLACE_CURRENT


def _console_event(event: str, **fields: object) -> None:
    parts = [f"[tomoko:ws-control] {event}"]
    for key, value in fields.items():
        text = str(value)
        if len(text) > 120:
            text = text[:117] + "..."
        parts.append(f"{key}={text!r}")
    print(" ".join(parts), flush=True)
