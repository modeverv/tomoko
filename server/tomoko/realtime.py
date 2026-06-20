from __future__ import annotations

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from server.shared.models import TurnMaterials
from server.tomoko.turn_state import TurnMaterialState

app = FastAPI(title="Tomoko v2 realtime control")
app.state.turn_material_state = TurnMaterialState()


@app.websocket("/internal/hot-path")
async def hot_path_realtime(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_json({"type": "ready", "process": "tomoko-realtime"})
    state: TurnMaterialState = app.state.turn_material_state
    try:
        while True:
            payload = await websocket.receive_json()
            event_type = payload.get("type")
            if event_type != "turn_materials":
                await websocket.send_json(
                    {"type": "error", "reason": "unsupported_event", "event": event_type}
                )
                continue
            materials_payload = dict(payload)
            materials_payload.pop("type", None)
            materials = TurnMaterials.from_dict(materials_payload)
            await state.update(materials)
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
    except WebSocketDisconnect:
        _console_event("ws_disconnected")


def _console_event(event: str, **fields: object) -> None:
    parts = [f"[tomoko:realtime] {event}"]
    for key, value in fields.items():
        text = str(value)
        if len(text) > 120:
            text = text[:117] + "..."
        parts.append(f"{key}={text!r}")
    print(" ".join(parts), flush=True)
