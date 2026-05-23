from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.edge.pipeline.vad import create_vad_processor
from server.session import TomoroSession

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[2]
CLIENT_DIR = ROOT_DIR / "client"

app = FastAPI(title="Tomoko Edge")
app.mount("/client", StaticFiles(directory=CLIENT_DIR), name="client")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(CLIENT_DIR / "index.html")


@app.websocket("/ws")
async def websocket_echo(websocket: WebSocket) -> None:
    await websocket.accept()
    chunk_count = 0

    async def send_event(event: dict[str, str]) -> None:
        await websocket.send_json(event)

    vad_processor_factory = getattr(app.state, "vad_processor_factory", create_vad_processor)
    session = TomoroSession(vad_processor=vad_processor_factory(), send_event=send_event)
    logger.info("phase2 websocket connected")
    try:
        while True:
            chunk = await websocket.receive_bytes()
            chunk_count += 1
            await session.process_audio_chunk(chunk)
            await websocket.send_bytes(chunk)
    except WebSocketDisconnect:
        logger.info("phase2 websocket disconnected after %s chunks", chunk_count)
