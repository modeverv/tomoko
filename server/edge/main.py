from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.edge.debug_recording import DebugAudioRecorder
from server.edge.participation.wake_word import WakeWordJudge
from server.edge.pipeline.stt import (
    SpeechTranscriber,
    create_stt_transcriber,
    warm_up_transcriber,
)
from server.edge.pipeline.stt_filter import TranscriptFilter
from server.edge.pipeline.stt_gate import SttAudioFrontend
from server.edge.pipeline.vad import create_vad_processor
from server.edge.remote import EdgeRemoteAudioSession, EdgeReplyPlayer
from server.gateway.candidate_commands import CandidateCommandRunner
from server.gateway.connections import ClientConnectionRegistry
from server.gateway.dedup import DuplicateSpeechFilter, PostgresRecentTranscriptReader
from server.gateway.edge_adapter import GatewayEdgeProtocolHandler
from server.gateway.gesture_audio import GestureAudioEmitter
from server.gateway.initiative_feedback import PostgresCandidateFeedbackStore
from server.gateway.initiative_policy import InitiativeLLMJudge
from server.gateway.maai_backchannel import (
    MaaiBackchannelTap,
    create_maai_backchannel_tap_from_env,
)
from server.gateway.presence import PresenceManager
from server.gateway.reply.speech_normalizer import ReplySpeechNormalizer
from server.gateway.research import (
    ResearchCommandRunner,
    ResearchMcpClient,
    ResearchResultSummarizer,
)
from server.gateway.resolver import DirectSpeakerResolver
from server.gateway.stop_ack import StopAckAudioProvider
from server.gateway.stop_intent import (
    EmbeddingStopIntentClassifier,
    LLMStopIntentClassifier,
    PostgresStopIntentStore,
    StopIntentClassifierWorker,
)
from server.gateway.thinking.deep import ThinkDeepMode
from server.gateway.thinking.fast import ThinkFastMode
from server.gateway.turn_taking.barge_in import BargeInDetector
from server.gateway.turn_taking.worker_client import TurnTakingWorkerClient
from server.session import TomoroSession
from server.shared.calendar import PostgresCalendarEventStore
from server.shared.candidate import PostgresCandidateStore
from server.shared.config import NodeConfig
from server.shared.db import (
    PostgresAmbientLogWriter,
    PostgresConversationLogWriter,
    PostgresConversationSessionStore,
)
from server.shared.edge_protocol import (
    EdgeHelloEvent,
    EdgePlaybackTelemetryEvent,
    parse_edge_event,
)
from server.shared.inference.embedding import EmbeddingBackend, create_embedding_backend
from server.shared.inference.router import InferenceRouter
from server.shared.inference.tts import create_tts_backend
from server.shared.inference.tts.base import TTSBackend
from server.shared.memory import (
    PostgresConversationMemoryStore,
    PostgresConversationSessionSummaryStore,
)
from server.shared.models import ConnectedOutputState, PlaybackTelemetry, SessionEvent
from server.shared.persona import PostgresPersonaSnapshotStore
from server.shared.presence import PostgresPresenceStore
from server.shared.research_results import PostgresResearchResultStore


def _configure_app_logging() -> None:
    log_level_name = os.environ.get("TOMOKO_LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)
    log_file_name = os.environ.get("TOMOKO_LOG_FILE", "logs/server.log")
    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)s:%(name)s:%(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    server_logger = logging.getLogger("server")
    server_logger.setLevel(log_level)
    server_logger.handlers = [
        handler
        for handler in server_logger.handlers
        if not getattr(handler, "_tomoko_app_handler", False)
    ]

    stderr_handler = logging.StreamHandler()
    stderr_handler.setLevel(log_level)
    stderr_handler.setFormatter(formatter)
    stderr_handler._tomoko_app_handler = True  # type: ignore[attr-defined]
    server_logger.addHandler(stderr_handler)

    if log_file_name:
        log_file = Path(log_file_name)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        file_handler._tomoko_app_handler = True  # type: ignore[attr-defined]
        server_logger.addHandler(file_handler)
    server_logger.propagate = False


_configure_app_logging()
logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[2]
CLIENT_DIR = ROOT_DIR / "client"
ASSETS_DIR = ROOT_DIR / "assets"
WORK_DIR = ROOT_DIR / "work"
CONFIG_PATH = Path(
    os.environ.get("TOMOKO_CONFIG", ROOT_DIR / "config" / "central_realtime.toml")
)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await _warm_up_app()
    yield


app = FastAPI(title="Tomoko Edge", lifespan=lifespan)
app.mount("/client", StaticFiles(directory=CLIENT_DIR), name="client")
app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")
_connection_registry = ClientConnectionRegistry()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(CLIENT_DIR / "index.html")


def _create_default_vad_processor():
    config = _load_config()
    return create_vad_processor(silence_ms=config.audio.vad_silence_ms)


def _create_default_stt_audio_frontend(sample_rate: int = 16000) -> SttAudioFrontend:
    return SttAudioFrontend(
        sample_rate=sample_rate,
        enabled_filters=("speech_bandpass", "signal_gate"),
    )


def _create_default_router() -> InferenceRouter:
    cached_router = getattr(app.state, "_default_router", None)
    if cached_router is not None:
        return cached_router
    config = _load_config()
    router = InferenceRouter(config=config)
    app.state._default_router = router
    return router


def _create_default_thinking_mode() -> ThinkFastMode:
    return ThinkFastMode(persona_path=ROOT_DIR / "prompts" / "base_persona.md")


def _create_default_deep_thinking_mode() -> ThinkDeepMode:
    return ThinkDeepMode(persona_path=ROOT_DIR / "prompts" / "base_persona.md")


def _create_default_tts_backend() -> TTSBackend:
    cached_backend = getattr(app.state, "_default_tts_backend", None)
    if cached_backend is not None:
        return cached_backend
    config = _load_config()
    backend = create_tts_backend(config.backends[config.inference.tts_backend])
    app.state._default_tts_backend = backend
    return backend


def _create_default_speech_normalizer() -> ReplySpeechNormalizer:
    cached_normalizer = getattr(app.state, "_default_speech_normalizer", None)
    if cached_normalizer is not None:
        return cached_normalizer
    normalizer = ReplySpeechNormalizer()
    app.state._default_speech_normalizer = normalizer
    return normalizer


def _is_speech_normalizer_enabled() -> bool:
    return _load_config().inference.speech_normalizer_enabled


def _create_default_embedding_backend() -> EmbeddingBackend | None:
    cached_backend = getattr(app.state, "_default_embedding_backend", None)
    if cached_backend is not None:
        return cached_backend
    config = _load_config()
    if config.inference.embedding_backend is None:
        return None
    backend = create_embedding_backend(config.backends[config.inference.embedding_backend])
    app.state._default_embedding_backend = backend
    return backend


def _create_default_memory_store() -> PostgresConversationMemoryStore:
    config = _load_config()
    return PostgresConversationMemoryStore(config.database.dsn)


def _create_default_session_summary_store() -> PostgresConversationSessionSummaryStore:
    config = _load_config()
    return PostgresConversationSessionSummaryStore(config.database.dsn)


def _create_default_persona_store() -> PostgresPersonaSnapshotStore:
    config = _load_config()
    return PostgresPersonaSnapshotStore(config.database.dsn)


def _create_default_calendar_store() -> PostgresCalendarEventStore:
    config = _load_config()
    return PostgresCalendarEventStore(config.database.dsn)


def _create_default_research_result_store() -> PostgresResearchResultStore:
    config = _load_config()
    return PostgresResearchResultStore(config.database.dsn)


def _create_default_research_mcp_client() -> ResearchMcpClient:
    command_text = os.environ.get("TOMOKO_RESEARCH_MCP_COMMAND")
    if command_text:
        command = tuple(shlex.split(command_text))
        cwd = None
    else:
        operator_dir = ROOT_DIR.parent / "tomoko-research-operator"
        command = ("uv", "run", "tomoko-research-mcp")
        cwd = operator_dir
    timeout_sec = float(os.environ.get("TOMOKO_RESEARCH_MCP_TIMEOUT_SEC", "180"))
    return ResearchMcpClient(command=command, timeout_sec=timeout_sec, cwd=cwd)


@app.websocket("/ws")
async def websocket_session(websocket: WebSocket) -> None:
    config = _load_config()
    if config.node.role == "edge" and config.node.gateway_ws_url:
        await _edge_browser_session(websocket, config)
        return
    await _central_browser_session(websocket)


async def _central_browser_session(websocket: WebSocket) -> None:
    await websocket.accept()
    config = _load_config()
    chunk_count = 0
    connection_id = f"browser:{uuid4()}"

    async def send_event(event: dict[str, str]) -> None:
        await websocket.send_json(event)

    async def send_audio(chunk: bytes) -> None:
        await websocket.send_bytes(chunk)

    vad_processor_factory = getattr(
        app.state, 
        "vad_processor_factory", 
        _create_default_vad_processor
    )
    transcriber_factory = getattr(app.state, "transcriber_factory", _create_default_transcriber)
    participation_judge_factory = getattr(
        app.state,
        "participation_judge_factory",
        WakeWordJudge,
    )
    ambient_log_writer_factory = getattr(
        app.state,
        "ambient_log_writer_factory",
        _create_default_ambient_log_writer,
    )
    conversation_log_writer_factory = getattr(
        app.state,
        "conversation_log_writer_factory",
        _create_default_conversation_log_writer,
    )
    router_factory = getattr(
        app.state,
        "router_factory",
        _create_default_router,
    )
    thinking_mode_factory = getattr(
        app.state,
        "thinking_mode_factory",
        _create_default_thinking_mode,
    )
    deep_thinking_mode_factory = getattr(
        app.state,
        "deep_thinking_mode_factory",
        _create_default_deep_thinking_mode,
    )
    tts_backend_factory = getattr(
        app.state,
        "tts_backend_factory",
        _create_default_tts_backend,
    )
    speech_normalizer_factory = getattr(app.state, "speech_normalizer_factory", None)
    embedding_backend_factory = getattr(
        app.state,
        "embedding_backend_factory",
        _create_default_embedding_backend,
    )
    memory_store_factory = getattr(
        app.state,
        "memory_store_factory",
        _create_default_memory_store,
    )
    session_summary_store_factory = getattr(
        app.state,
        "session_summary_store_factory",
        _create_default_session_summary_store,
    )
    persona_store_factory = getattr(
        app.state,
        "persona_store_factory",
        _create_default_persona_store,
    )
    calendar_store_factory = getattr(
        app.state,
        "calendar_store_factory",
        _create_default_calendar_store,
    )
    research_result_store_factory = getattr(
        app.state,
        "research_result_store_factory",
        _create_default_research_result_store,
    )
    conversation_session_store_factory = getattr(
        app.state,
        "conversation_session_store_factory",
        _create_default_conversation_session_store,
    )
    candidate_store_factory = getattr(
        app.state,
        "candidate_store_factory",
        _create_default_candidate_store,
    )
    candidate_feedback_store_factory = getattr(
        app.state,
        "candidate_feedback_store_factory",
        _create_default_candidate_feedback_store,
    )
    stop_intent_store_factory = getattr(
        app.state,
        "stop_intent_store_factory",
        _create_default_stop_intent_store,
    )
    stop_ack_audio_provider_factory = getattr(
        app.state,
        "stop_ack_audio_provider_factory",
        _create_default_stop_ack_audio_provider,
    )
    barge_in_detector_factory = getattr(
        app.state,
        "barge_in_detector_factory",
        BargeInDetector,
    )
    audio_interaction_tap_factory = getattr(
        app.state,
        "audio_interaction_tap_factory",
        create_maai_backchannel_tap_from_env,
    )
    vad_processor = vad_processor_factory()
    candidate_feedback_store = candidate_feedback_store_factory()
    stop_intent_store = stop_intent_store_factory()
    router = router_factory()
    embedding_backend = embedding_backend_factory()
    audio_interaction_tap = audio_interaction_tap_factory()
    tts_backend = tts_backend_factory()
    output_state = _connection_registry.register(
        connection_id=connection_id,
        device_id=vad_processor.device_id,
        role="browser",
        can_receive_audio=True,
        can_receive_display=True,
    )
    session = TomoroSession(
        vad_processor=vad_processor,
        send_event=send_event,
        send_audio=send_audio,
        transcriber=transcriber_factory(),
        participation_judge=participation_judge_factory(),
        ambient_log_writer=ambient_log_writer_factory(),
        conversation_log_writer=conversation_log_writer_factory(),
        conversation_session_store=conversation_session_store_factory(),
        router=router,
        thinking_mode=thinking_mode_factory(),
        deep_thinking_mode=deep_thinking_mode_factory(),
        tts_backend=tts_backend,
        embedding_backend=embedding_backend,
        memory_store=memory_store_factory(),
        session_summary_store=session_summary_store_factory(),
        persona_store=persona_store_factory(),
        calendar_store=calendar_store_factory(),
        research_result_store=research_result_store_factory(),
        speech_normalizer=(
            speech_normalizer_factory()
            if speech_normalizer_factory is not None
            else (
                _create_default_speech_normalizer()
                if _is_speech_normalizer_enabled()
                else None
            )
        ),
        barge_in_detector=barge_in_detector_factory(),
        turn_taking_judge=TurnTakingWorkerClient(
            url=os.environ.get(
                "TOMOKO_TURN_TAKING_WORKER_URL",
                "http://127.0.0.1:8765/judge",
            ),
            timeout_ms=int(os.environ.get("TOMOKO_TURN_TAKING_TIMEOUT_MS", "180")),
        ),
        transcript_filter=TranscriptFilter(),
        stt_audio_frontend=_create_default_stt_audio_frontend(vad_processor.sample_rate),
        candidate_feedback_store=candidate_feedback_store,
        stop_intent_store=stop_intent_store,
        stop_ack_audio_provider=stop_ack_audio_provider_factory(),
        connected_output_state=output_state,
        audio_interaction_tap=audio_interaction_tap,
    )
    research_summary_backend = await router.select("session_summary")
    research_runner = ResearchCommandRunner(
        session=session,
        client=_create_default_research_mcp_client(),
        result_store=research_result_store_factory(),
        embedding_backend=embedding_backend,
        summarizer=ResearchResultSummarizer(backend=research_summary_backend),
    )
    session.set_research_transition_handler(research_runner.run_result)
    gesture_audio_emitter = GestureAudioEmitter(
        state_provider=session.get_now_state,
        send_audio=send_audio,
        send_event=send_event,
        tts_backend=tts_backend,
        audio_observer=audio_interaction_tap,
    )
    if isinstance(audio_interaction_tap, MaaiBackchannelTap):
        audio_interaction_tap.set_suggestion_callback(
            gesture_audio_emitter.release_backchannel
        )
        await audio_interaction_tap.start()
    debug_recorder_factory = getattr(
        app.state,
        "debug_recorder_factory",
        lambda: DebugAudioRecorder(
            root=WORK_DIR,
            transcriber=transcriber_factory(),
            sample_rate=config.audio.sample_rate,
        ),
    )
    debug_recorder = debug_recorder_factory()
    candidate_runner = CandidateCommandRunner(
        session=session,
        store=candidate_store_factory(),
        device_id=session.vad_processor.device_id,
        feedback_store=candidate_feedback_store,
        llm_judge=InitiativeLLMJudge(router),
    )
    await candidate_runner.run_result(
        await session.post_event(
            SessionEvent(
                type="session_started",
                payload={"device_id": session.vad_processor.device_id},
            )
        )
    )
    initiative_task = asyncio.create_task(
        _initiative_idle_loop(session, candidate_runner)
    )
    stop_intent_worker = StopIntentClassifierWorker(
        store=stop_intent_store,
        embedding_classifier=EmbeddingStopIntentClassifier(embedding_backend),
        llm_classifier=LLMStopIntentClassifier(router),
        result_callback=session.apply_stop_intent_event,
    )
    stop_intent_task = asyncio.create_task(stop_intent_worker.run_forever())
    logger.info("phase4 websocket connected")
    try:
        while True:
            message = await websocket.receive()
            if message.get("bytes") is not None:
                chunk = message["bytes"]
                chunk_count += 1
                if debug_recorder.is_recording:
                    should_stop = debug_recorder.add_chunk(chunk)
                    if should_stop:
                        result = await debug_recorder.stop()
                        await websocket.send_json(result.to_event())
                    continue
                await session.process_audio_chunk(chunk)
                continue
            if message.get("text") is not None:
                await _handle_client_text_event(
                    session,
                    message["text"],
                    debug_recorder=debug_recorder,
                    send_event=websocket.send_json,
                )
                continue
            if message.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect()
            chunk_count += 1
    except WebSocketDisconnect:
        logger.info("phase4 websocket disconnected after %s chunks", chunk_count)
    finally:
        output_state = _connection_registry.unregister(connection_id)
        await session.apply_client_lifecycle_event(
            SessionEvent(
                type="connected_output_state_changed",
                payload={"output_state": output_state},
            )
        )
        initiative_task.cancel()
        stop_intent_worker.stop()
        stop_intent_task.cancel()
        if audio_interaction_tap is not None:
            stop = getattr(audio_interaction_tap, "stop", None)
            if stop is not None:
                maybe_awaitable = stop()
                if asyncio.iscoroutine(maybe_awaitable):
                    await maybe_awaitable
        with suppress(asyncio.CancelledError):
            await initiative_task
        with suppress(asyncio.CancelledError):
            await stop_intent_task


@app.websocket("/edge/ws")
async def edge_gateway_session(websocket: WebSocket) -> None:
    await websocket.accept()
    device_id = "unknown"
    connection_id = f"edge:{uuid4()}"

    async def send_event(event: dict[str, str]) -> None:
        await websocket.send_json(event)

    session = _create_gateway_text_session(send_event)
    config = _load_config()
    presence_store = PostgresPresenceStore(config.database.dsn)
    handler = GatewayEdgeProtocolHandler(
        session=session,
        presence_manager=PresenceManager(
            store=presence_store,
            resolver=DirectSpeakerResolver(),
        ),
        duplicate_filter=DuplicateSpeechFilter(
            reader=PostgresRecentTranscriptReader(config.database.dsn),
        ),
    )
    candidate_runner = CandidateCommandRunner(
        session=session,
        store=_create_default_candidate_store(),
        device_id=device_id,
        feedback_store=_create_default_candidate_feedback_store(),
        llm_judge=InitiativeLLMJudge(_create_default_router()),
    )
    logger.info("edge gateway websocket connected")
    try:
        while True:
            message = await websocket.receive()
            if message.get("text") is not None:
                try:
                    event = parse_edge_event(json.loads(message["text"]))
                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning("ignored malformed edge event error=%s", e)
                    continue
                if isinstance(event, EdgeHelloEvent):
                    device_id = event.device_id
                    candidate_runner.device_id = device_id
                    output_state = _connection_registry.register(
                        connection_id=connection_id,
                        device_id=device_id,
                        role="edge",
                        can_receive_audio=True,
                        can_receive_display=True,
                    )
                    await session.post_event(
                        SessionEvent(
                            type="connected_output_state_changed",
                            payload={"output_state": output_state},
                        )
                    )
                    await candidate_runner.run_result(
                        await session.post_event(
                            SessionEvent(
                                type="session_started",
                                payload={"device_id": device_id},
                            )
                        )
                    )
                await handler.handle(event)
                continue
            if message.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect()
    except WebSocketDisconnect:
        logger.info("edge gateway websocket disconnected device_id=%s", device_id)
    finally:
        output_state = _connection_registry.unregister(connection_id)
        await session.apply_client_lifecycle_event(
            SessionEvent(
                type="connected_output_state_changed",
                payload={"output_state": output_state},
            )
        )


async def _edge_browser_session(websocket: WebSocket, config: NodeConfig) -> None:
    await websocket.accept()
    import websockets

    assert config.node.device_id is not None
    assert config.node.gateway_ws_url is not None
    device_id = config.node.device_id

    async def send_browser_event(event: dict[str, object]) -> None:
        await websocket.send_json(event)

    async def send_browser_audio(chunk: bytes) -> None:
        await websocket.send_bytes(chunk)

    async with websockets.connect(config.node.gateway_ws_url) as gateway:
        await gateway.send(json.dumps(EdgeHelloEvent(device_id=device_id).to_json()))

        async def send_gateway_event(event: dict[str, object]) -> None:
            await gateway.send(json.dumps(event))

        edge_session = EdgeRemoteAudioSession(
            device_id=device_id,
            vad_processor=(edge_vad_processor := _create_default_vad_processor()),
            transcriber=_create_default_transcriber(),
            transcript_filter=TranscriptFilter(),
            send_browser_event=send_browser_event,
            send_gateway_event=send_gateway_event,
            stt_audio_frontend=_create_default_stt_audio_frontend(
                edge_vad_processor.sample_rate
            ),
        )
        reply_player = EdgeReplyPlayer(
            tts_backend=_create_default_tts_backend(),
            send_browser_event=send_browser_event,
            send_browser_audio=send_browser_audio,
        )

        async def receive_gateway_events() -> None:
            async for payload in gateway:
                await reply_player.handle_gateway_payload(payload)

        gateway_task = asyncio.create_task(receive_gateway_events())
        try:
            while True:
                message = await websocket.receive()
                if message.get("bytes") is not None:
                    await edge_session.process_audio_chunk(message["bytes"])
                    continue
                if message.get("text") is not None:
                    await _forward_edge_playback_event(
                        text=message["text"],
                        device_id=device_id,
                        send_gateway_event=send_gateway_event,
                    )
                    continue
                if message.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect()
        except WebSocketDisconnect:
            logger.info("edge browser websocket disconnected device_id=%s", device_id)
        finally:
            gateway_task.cancel()
            with suppress(asyncio.CancelledError):
                await gateway_task


async def _forward_edge_playback_event(
    *,
    text: str,
    device_id: str,
    send_gateway_event,
) -> None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("ignored non-json edge browser text event")
        return
    event_type = payload.get("type")
    if event_type not in {"playback_started", "playback_ended"}:
        return
    await send_gateway_event(
        EdgePlaybackTelemetryEvent(
            type=event_type,
            device_id=device_id,
            turn_id=payload.get("turn_id"),
            chunk_id=_optional_int(payload.get("chunk_id")),
            scheduled_audio_time=_optional_float(payload.get("scheduled_audio_time")),
            sent_audio_time=_optional_float(payload.get("sent_audio_time")),
            audio_context_time=_optional_float(payload.get("audio_context_time")),
            performance_now_ms=_optional_float(payload.get("performance_now_ms")),
        ).to_json()
    )


def _create_gateway_text_session(
    send_event,
    connected_output_state: ConnectedOutputState | None = None,
) -> TomoroSession:
    return TomoroSession(
        vad_processor=_create_default_vad_processor(),
        send_event=send_event,
        send_audio=None,
        transcriber=None,
        participation_judge=WakeWordJudge(),
        ambient_log_writer=_create_default_ambient_log_writer(),
        conversation_log_writer=_create_default_conversation_log_writer(),
        conversation_session_store=_create_default_conversation_session_store(),
        router=_create_default_router(),
        thinking_mode=_create_default_thinking_mode(),
        deep_thinking_mode=_create_default_deep_thinking_mode(),
        tts_backend=None,
        embedding_backend=_create_default_embedding_backend(),
        memory_store=_create_default_memory_store(),
        session_summary_store=_create_default_session_summary_store(),
        persona_store=_create_default_persona_store(),
        calendar_store=_create_default_calendar_store(),
        research_result_store=_create_default_research_result_store(),
        speech_normalizer=None,
        barge_in_detector=BargeInDetector(),
        turn_taking_judge=TurnTakingWorkerClient(
            url=os.environ.get(
                "TOMOKO_TURN_TAKING_WORKER_URL",
                "http://127.0.0.1:8765/judge",
            ),
            timeout_ms=int(os.environ.get("TOMOKO_TURN_TAKING_TIMEOUT_MS", "180")),
        ),
        transcript_filter=TranscriptFilter(),
        candidate_feedback_store=_create_default_candidate_feedback_store(),
        stop_intent_store=_create_default_stop_intent_store(),
        stop_ack_audio_provider=_create_default_stop_ack_audio_provider(),
        connected_output_state=connected_output_state,
    )


def _load_config() -> NodeConfig:
    config_factory = getattr(app.state, "config_factory", None)
    if config_factory is not None:
        return config_factory()
    return NodeConfig.load(CONFIG_PATH)


def _create_default_transcriber() -> SpeechTranscriber:
    config = _load_config()
    if config.inference.stt_backend is None:
        raise ValueError("stt_backend is not configured")
    return create_stt_transcriber(config.backends[config.inference.stt_backend])


async def _warm_up_app() -> None:
    if getattr(app.state, "skip_warm_up", False):
        logger.info("startup warm-up skipped")
        return

    config = _load_config()
    if config.inference.stt_backend is None:
        logger.info("startup warm-up skipped: stt_backend is not configured")
        return

    backend_name = config.inference.stt_backend
    spec = config.backends[backend_name]
    started_at = time.perf_counter()
    logger.info(
        "startup warm-up started target=stt backend=%s type=%s model=%s",
        backend_name,
        spec.type,
        spec.model,
    )
    transcriber_factory = getattr(app.state, "transcriber_factory", _create_default_transcriber)
    await warm_up_transcriber(transcriber_factory())
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    logger.info(
        "startup warm-up completed target=stt backend=%s elapsed_ms=%.1f",
        backend_name,
        elapsed_ms,
    )

    tts_backend_name = config.inference.tts_backend
    tts_spec = config.backends[tts_backend_name]
    started_at = time.perf_counter()
    logger.info(
        "startup warm-up started target=tts backend=%s type=%s model=%s",
        tts_backend_name,
        tts_spec.type,
        tts_spec.model,
    )
    tts_backend_factory = getattr(app.state, "tts_backend_factory", _create_default_tts_backend)
    await tts_backend_factory().warm_up()
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    logger.info(
        "startup warm-up completed target=tts backend=%s elapsed_ms=%.1f",
        tts_backend_name,
        elapsed_ms,
    )

    if config.node.role == "edge" and config.node.gateway_ws_url:
        logger.info("startup warm-up completed role=edge skipped central inference targets")
        return

    conversation_backend_name = config.inference.conversation_backend
    conversation_spec = config.backends[conversation_backend_name]
    started_at = time.perf_counter()
    logger.info(
        "startup warm-up started target=conversation backend=%s type=%s model=%s",
        conversation_backend_name,
        conversation_spec.type,
        conversation_spec.model,
    )
    router_factory = getattr(app.state, "router_factory", _create_default_router)
    conversation_backend = await router_factory().select("conversation", "privacy")
    warm_up = getattr(conversation_backend, "warm_up", None)
    if warm_up is not None:
        await warm_up()
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    logger.info(
        "startup warm-up completed target=conversation backend=%s elapsed_ms=%.1f",
        conversation_backend.name,
        elapsed_ms,
    )

    if config.inference.embedding_backend is not None:
        embedding_backend_name = config.inference.embedding_backend
        embedding_spec = config.backends[embedding_backend_name]
        started_at = time.perf_counter()
        logger.info(
            "startup warm-up started target=embedding backend=%s type=%s model=%s",
            embedding_backend_name,
            embedding_spec.type,
            embedding_spec.model,
        )
        embedding_backend_factory = getattr(
            app.state,
            "embedding_backend_factory",
            _create_default_embedding_backend,
        )
        embedding_backend = embedding_backend_factory()
        if embedding_backend is not None:
            await embedding_backend.warm_up()
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        logger.info(
            "startup warm-up completed target=embedding backend=%s elapsed_ms=%.1f",
            embedding_backend_name,
            elapsed_ms,
        )

    if not config.inference.speech_normalizer_enabled:
        logger.info("startup warm-up skipped target=tts_text_normalizer disabled=true")
        return
    speech_normalizer_factory = getattr(
        app.state,
        "speech_normalizer_factory",
        _create_default_speech_normalizer,
    )
    started_at = time.perf_counter()
    logger.info("startup warm-up started target=tts_text_normalizer model=gemma4-e2b")
    await speech_normalizer_factory().warm_up()
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    logger.info(
        "startup warm-up completed target=tts_text_normalizer elapsed_ms=%.1f",
        elapsed_ms,
    )


def _create_default_ambient_log_writer() -> PostgresAmbientLogWriter:
    config = _load_config()
    return PostgresAmbientLogWriter(config.database.dsn)


def _create_default_conversation_log_writer() -> PostgresConversationLogWriter:
    config = _load_config()
    return PostgresConversationLogWriter(config.database.dsn)


def _create_default_conversation_session_store() -> PostgresConversationSessionStore:
    config = _load_config()
    return PostgresConversationSessionStore(config.database.dsn)


def _create_default_candidate_store() -> PostgresCandidateStore:
    config = _load_config()
    return PostgresCandidateStore(config.database.dsn)


def _create_default_candidate_feedback_store() -> PostgresCandidateFeedbackStore:
    config = _load_config()
    return PostgresCandidateFeedbackStore(config.database.dsn)


def _create_default_stop_intent_store() -> PostgresStopIntentStore:
    config = _load_config()
    return PostgresStopIntentStore(config.database.dsn)


def _create_default_stop_ack_audio_provider() -> StopAckAudioProvider:
    return StopAckAudioProvider(ROOT_DIR / "assets" / "audio" / "stop_ack.wav")


async def _initiative_idle_loop(
    session: TomoroSession,
    candidate_runner: CandidateCommandRunner,
) -> None:
    while True:
        await asyncio.sleep(45)
        result = await session.post_event(SessionEvent(type="idle_timer_elapsed"))
        await candidate_runner.run_result(result)


async def _handle_client_text_event(
    session: TomoroSession,
    text: str,
    *,
    debug_recorder: DebugAudioRecorder | None = None,
    send_event=None,
) -> None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("ignored non-json websocket text event")
        return
    event_type = payload.get("type")
    if event_type == "debug_recording_start":
        if debug_recorder is None or send_event is None:
            logger.warning("ignored debug recording start without recorder")
            return
        try:
            event = debug_recorder.start(
                kind=str(payload.get("kind") or "noise"),
                duration_ms=_optional_int(payload.get("duration_ms")),
                expected_text=_optional_str(payload.get("expected_text")),
            )
        except ValueError as e:
            await send_event({"type": "debug_recording_error", "error": str(e)})
            return
        await send_event(event)
        return
    if event_type == "debug_recording_stop":
        if debug_recorder is None or send_event is None:
            logger.warning("ignored debug recording stop without recorder")
            return
        try:
            result = await debug_recorder.stop()
        except ValueError as e:
            await send_event({"type": "debug_recording_error", "error": str(e)})
            return
        await send_event(result.to_event())
        return
    if event_type == "client_stop":
        await session.apply_client_lifecycle_event(
            SessionEvent(
                type="client_stop_requested",
                payload={"reason": "ui_stop"},
            )
        )
        return
    if event_type not in {"playback_started", "playback_ended"}:
        logger.warning("ignored unknown websocket text event type=%s", event_type)
        return
    await session.handle_playback_telemetry(
        PlaybackTelemetry(
            type=event_type,
            turn_id=payload.get("turn_id"),
            chunk_id=_optional_int(payload.get("chunk_id")),
            scheduled_audio_time=_optional_float(payload.get("scheduled_audio_time")),
            sent_audio_time=_optional_float(payload.get("sent_audio_time")),
            audio_context_time=_optional_float(payload.get("audio_context_time")),
            performance_now_ms=_optional_float(payload.get("performance_now_ms")),
        )
    )


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None
