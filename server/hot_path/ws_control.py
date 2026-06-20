from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any

import websockets

from server.hot_path.speech_executor import prompt_request_for_order
from server.shared.models import (
    ModelOutputEvent,
    PartialTranscriptObservation,
    SemanticSaturationResult,
    SpeechOrder,
    SpeechSchedulerAction,
    SpeechSchedulerOutput,
    SpeechTextIntent,
    TurnMaterials,
)
from server.tomoko.conversation import TomokoConversationResult


@dataclass(slots=True)
class RemoteTomokoWsCore:
    url: str = field(default_factory=lambda: os.environ.get("TOMOKO_INTERNAL_WS_URL", ""))
    request_timeout_sec: float = 30.0
    _ws: Any | None = field(default=None, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _latest_materials: TurnMaterials | None = None

    def update_turn_materials(self, materials: TurnMaterials) -> None:
        self._latest_materials = materials

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
            ws = await self._ensure_ws()
            if self._latest_materials is not None:
                await ws.send(
                    json.dumps(
                        {"type": "turn_materials", **self._latest_materials.to_dict()},
                        ensure_ascii=False,
                    )
                )
                await self._receive_expected(ws, "turn_materials_ack")
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
            await ws.send(
                json.dumps(
                    {"type": "stt_observation", **observation.to_dict()},
                    ensure_ascii=False,
                )
            )
            ack = await self._receive_expected(ws, "stt_observation_ack")
            order: SpeechOrder | None = None
            action = SpeechSchedulerAction(str(ack.get("action", "suppress")))
            while True:
                try:
                    message = await asyncio.wait_for(
                        ws.recv(),
                        timeout=0.05,
                    )
                except TimeoutError:
                    break
                event = json.loads(message)
                event_type = event.get("type")
                if event_type == "speech_order":
                    payload = dict(event)
                    payload.pop("type", None)
                    order = SpeechOrder.from_dict(payload)
                    action = _action_for_order(order)
                    break
                if event_type == "cancel_order":
                    action = SpeechSchedulerAction.STOP
                    break
                if event_type == "error":
                    raise RuntimeError(str(event))
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
            )

    async def aclose(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def _ensure_ws(self) -> Any:
        if self._ws is None:
            self._ws = await websockets.connect(self.url)
            await self._receive_expected(self._ws, "ready")
        return self._ws

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
