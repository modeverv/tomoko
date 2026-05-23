from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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
    logger.info("phase1 websocket connected")
    try:
        while True:
            chunk = await websocket.receive_bytes()
            chunk_count += 1
            await websocket.send_bytes(chunk)
    except WebSocketDisconnect:
        logger.info("phase1 websocket disconnected after %s chunks", chunk_count)
