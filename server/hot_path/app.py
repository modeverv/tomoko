from __future__ import annotations

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.hot_path.protocol import encode_server_event, parse_browser_message

app = FastAPI(title="Tomoko v2 hot-path-process")
app.mount("/client", StaticFiles(directory="client"), name="client")


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
            if "bytes" in message and message["bytes"] is not None:
                await websocket.send_text(
                    encode_server_event("debug_marker", received="audio_bytes")
                )
                continue
            if "text" in message and message["text"] is not None:
                event = parse_browser_message(message["text"])
                if not isinstance(event, bytes) and event.event_type == "audio_control":
                    await websocket.send_text(encode_server_event("audio_control_ack"))
    except WebSocketDisconnect:
        return
