from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.edge.participation.wake_word import WakeWordJudge
from server.edge.pipeline.stt import (
    SpeechTranscriber,
    create_stt_transcriber,
    warm_up_transcriber,
)
from server.edge.pipeline.stt_filter import TranscriptFilter
from server.edge.pipeline.vad import create_vad_processor
from server.gateway.reply.speech_normalizer import ReplySpeechNormalizer
from server.gateway.thinking.deep import ThinkDeepMode
from server.gateway.thinking.fast import ThinkFastMode
from server.gateway.turn_taking.barge_in import BargeInDetector
from server.session import TomoroSession
from server.shared.config import NodeConfig
from server.shared.db import PostgresAmbientLogWriter, PostgresConversationLogWriter
from server.shared.inference.embedding import EmbeddingBackend, create_embedding_backend
from server.shared.inference.router import InferenceRouter
from server.shared.inference.tts import create_tts_backend
from server.shared.inference.tts.base import TTSBackend
from server.shared.memory import PostgresConversationMemoryStore
from server.shared.models import PlaybackTelemetry


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
CONFIG_PATH = ROOT_DIR / "config" / "central_realtime.toml"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await _warm_up_app()
    yield


app = FastAPI(title="Tomoko Edge", lifespan=lifespan)
app.mount("/client", StaticFiles(directory=CLIENT_DIR), name="client")
app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(CLIENT_DIR / "index.html")


def _create_default_vad_processor():
    config = _load_config()
    return create_vad_processor(silence_ms=config.audio.vad_silence_ms)


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


@app.websocket("/ws")
async def websocket_session(websocket: WebSocket) -> None:
    await websocket.accept()
    chunk_count = 0

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
    barge_in_detector_factory = getattr(
        app.state,
        "barge_in_detector_factory",
        BargeInDetector,
    )
    session = TomoroSession(
        vad_processor=vad_processor_factory(),
        send_event=send_event,
        send_audio=send_audio,
        transcriber=transcriber_factory(),
        participation_judge=participation_judge_factory(),
        ambient_log_writer=ambient_log_writer_factory(),
        conversation_log_writer=conversation_log_writer_factory(),
        router=router_factory(),
        thinking_mode=thinking_mode_factory(),
        deep_thinking_mode=deep_thinking_mode_factory(),
        tts_backend=tts_backend_factory(),
        embedding_backend=embedding_backend_factory(),
        memory_store=memory_store_factory(),
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
        transcript_filter=TranscriptFilter(),
    )
    logger.info("phase4 websocket connected")
    try:
        while True:
            message = await websocket.receive()
            if message.get("bytes") is not None:
                chunk = message["bytes"]
                chunk_count += 1
                await session.process_audio_chunk(chunk)
                continue
            if message.get("text") is not None:
                await _handle_client_text_event(session, message["text"])
                continue
            if message.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect()
            chunk_count += 1
    except WebSocketDisconnect:
        logger.info("phase4 websocket disconnected after %s chunks", chunk_count)


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


async def _handle_client_text_event(session: TomoroSession, text: str) -> None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("ignored non-json websocket text event")
        return
    event_type = payload.get("type")
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
