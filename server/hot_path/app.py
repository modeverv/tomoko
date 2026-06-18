from __future__ import annotations

import os
from collections.abc import Callable
from typing import TypeVar

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.audio.stt import StaticStreamingSttBackend, StreamingSttEvent
from server.audio.vad import VADProcessor
from server.hot_path.audio_conversation import (
    HotPathAudioConversation,
    HotPathConversationResult,
    create_default_audio_conversation,
)
from server.hot_path.model_executor import (
    PromptExecutionResult,
    PromptExecutor,
    StaticChatBackend,
    StaticWavTtsBackend,
    create_default_real_prompt_executor,
)
from server.hot_path.protocol import encode_server_event, parse_browser_message
from server.shared.models import CancelPolicy, PromptRequest, PromptScope
from server.tomoko.main import TomokoProcessCore
from server.tomoko.prompt import PromptBuilderV2
from server.tomoko.session import SessionBoundaryModel

app = FastAPI(title="Tomoko v2 hot-path-process")
app.mount("/client", StaticFiles(directory="client"), name="client")

EnumT = TypeVar("EnumT")
PROMPT_EVENT_TYPES = frozenset({"prompt", "text_prompt", "user_text"})


@app.get("/")
async def index() -> FileResponse:
    return FileResponse("client/index.html")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_text(encode_server_event("ready", process="hot-path"))
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            if "bytes" in message and message["bytes"] is not None:
                result = await _audio_conversation().process_audio_bytes(message["bytes"])
                if result is None:
                    await websocket.send_text(
                        encode_server_event("debug_marker", received="audio_bytes")
                    )
                else:
                    await _send_audio_conversation_result(websocket, result)
                continue
            if "text" in message and message["text"] is not None:
                event = parse_browser_message(message["text"])
                if not isinstance(event, bytes) and event.event_type == "audio_control":
                    await websocket.send_text(encode_server_event("audio_control_ack"))
                    continue
                if not isinstance(event, bytes) and event.event_type in PROMPT_EVENT_TYPES:
                    await _run_prompt(websocket, _prompt_request_from_event(event.payload))
    except WebSocketDisconnect:
        return


async def _send_audio_conversation_result(
    websocket: WebSocket,
    result: HotPathConversationResult,
) -> None:
    for observation in result.observations:
        await websocket.send_text(
            encode_server_event(
                "transcript",
                text=observation.text,
                is_final=observation.is_final,
                observation_id=str(observation.id),
            )
        )
    if result.durable_utterance is not None:
        await websocket.send_text(
            encode_server_event(
                "durable_utterance",
                text=result.durable_utterance.text,
                session_id=str(result.durable_utterance.session_id),
                utterance_id=str(result.durable_utterance.id),
            )
        )
    if result.prompt_request is None:
        return
    await _send_prompt_execution_result(websocket, result.prompt_request, result.execution_result)


async def _run_prompt(websocket: WebSocket, request: PromptRequest) -> None:
    try:
        result = await _prompt_executor().execute(request)
    except Exception as exc:
        await websocket.send_text(
            encode_server_event(
                "prompt_error",
                request_id=str(request.id),
                error=type(exc).__name__,
                message=str(exc),
            )
        )
        return

    await _send_prompt_execution_result(websocket, request, result)


async def _send_prompt_execution_result(
    websocket: WebSocket,
    request: PromptRequest,
    result: PromptExecutionResult,
) -> None:
    for event in result.model_events:
        if event.event_kind == "delta":
            await websocket.send_text(
                encode_server_event(
                    "model_delta",
                    request_id=str(event.request_id),
                    text_delta=event.text_delta,
                )
            )
        elif event.event_kind == "complete":
            await websocket.send_text(
                encode_server_event(
                    "model_complete",
                    request_id=str(event.request_id),
                    text=event.text,
                )
            )

    for chunk in result.audio_chunks:
        await websocket.send_bytes(chunk.chunk)
        if chunk.is_final:
            await websocket.send_text(
                encode_server_event(
                    "audio_complete",
                    request_id=str(chunk.request_id),
                    sample_rate=chunk.sample_rate,
                    content_type=chunk.content_type,
                )
            )

    await websocket.send_text(encode_server_event("prompt_complete", request_id=str(request.id)))


def _prompt_executor() -> PromptExecutor:
    executor = getattr(app.state, "prompt_executor", None)
    if executor is None:
        executor = (
            _fake_prompt_executor()
            if _fake_runtime_enabled()
            else create_default_real_prompt_executor()
        )
        app.state.prompt_executor = executor
    return executor


def _audio_conversation() -> HotPathAudioConversation:
    conversation = getattr(app.state, "audio_conversation", None)
    if conversation is None:
        if _fake_runtime_enabled():
            conversation = HotPathAudioConversation(
                vad=VADProcessor(),
                stt_backend=StaticStreamingSttBackend(
                    [
                        StreamingSttEvent(
                            os.environ.get("TOMOKO_V2_FAKE_TRANSCRIPT", "トモコ、返事して"),
                            True,
                            1.0,
                        )
                    ]
                ),
                tomoko_core=TomokoProcessCore(SessionBoundaryModel()),
                prompt_builder=PromptBuilderV2(),
                prompt_executor=_prompt_executor(),
            )
        else:
            conversation = create_default_audio_conversation(_prompt_executor())
        app.state.audio_conversation = conversation
    return conversation


def _fake_runtime_enabled() -> bool:
    return os.environ.get("TOMOKO_V2_FAKE_RUNTIME") == "1"


def _fake_prompt_executor() -> PromptExecutor:
    return PromptExecutor(
        StaticChatBackend([os.environ.get("TOMOKO_V2_FAKE_REPLY", "うん、聞こえてるよ。")]),
        StaticWavTtsBackend([b"RIFFxxxxWAVEdata"]),
    )


def _prompt_request_from_event(payload: dict[str, object]) -> PromptRequest:
    prompt_text = payload.get("prompt_text") or payload.get("text") or payload.get("message")
    if not isinstance(prompt_text, str) or not prompt_text.strip():
        raise ValueError("prompt event requires text")
    return PromptRequest(
        prompt_text=prompt_text,
        scope=_enum_or_default(PromptScope, payload.get("scope"), PromptScope.SHORT),
        decision_id=None,
        utterance_id=None,
        candidate_id=None,
        priority=int(payload.get("priority", 50)),
        cancel_policy=_enum_or_default(
            CancelPolicy,
            payload.get("cancel_policy"),
            CancelPolicy.KEEP_UNTIL_COMPLETE,
        ),
    )


def _enum_or_default(
    enum_factory: Callable[[str], EnumT],
    value: object,
    default: EnumT,
) -> EnumT:
    if value is None:
        return default
    try:
        return enum_factory(str(value))
    except ValueError:
        return default
