from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.edge.participation.wake_word import WakeWordJudge
from server.edge.pipeline.stt import SpeechTranscriber, create_stt_transcriber
from server.edge.pipeline.vad import create_vad_processor
from server.session import TomoroSession
from server.shared.config import NodeConfig
from server.shared.db import PostgresAmbientLogWriter

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[2]
CLIENT_DIR = ROOT_DIR / "client"
CONFIG_PATH = ROOT_DIR / "config" / "central_realtime.toml"

app = FastAPI(title="Tomoko Edge")
app.mount("/client", StaticFiles(directory=CLIENT_DIR), name="client")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(CLIENT_DIR / "index.html")


def _create_default_vad_processor():
    config = _load_config()
    return create_vad_processor(silence_ms=config.audio.vad_silence_ms)

@app.websocket("/ws")
async def websocket_session(websocket: WebSocket) -> None:
    await websocket.accept()
    chunk_count = 0

    async def send_event(event: dict[str, str]) -> None:
        await websocket.send_json(event)

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
    session = TomoroSession(
        vad_processor=vad_processor_factory(),
        send_event=send_event,
        transcriber=transcriber_factory(),
        participation_judge=participation_judge_factory(),
        ambient_log_writer=ambient_log_writer_factory(),
    )
    logger.info("phase3 websocket connected")
    try:
        while True:
            chunk = await websocket.receive_bytes()
            chunk_count += 1
            await session.process_audio_chunk(chunk)
    except WebSocketDisconnect:
        logger.info("phase3 websocket disconnected after %s chunks", chunk_count)


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


def _create_default_ambient_log_writer() -> PostgresAmbientLogWriter:
    config = _load_config()
    return PostgresAmbientLogWriter(config.database.dsn)
