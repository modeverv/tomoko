from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class BrowserJsonEvent:
    event_type: str
    payload: dict[str, Any]


def parse_browser_message(message: str | bytes) -> BrowserJsonEvent | bytes:
    if isinstance(message, bytes):
        return message
    payload = json.loads(message)
    if not isinstance(payload, dict) or "type" not in payload:
        raise ValueError("browser JSON event must contain type")
    return BrowserJsonEvent(
        event_type=str(payload["type"]),
        payload={key: value for key, value in payload.items() if key != "type"},
    )


def encode_server_event(event_type: str, **payload: Any) -> str:
    return json.dumps({"type": event_type, **payload}, ensure_ascii=False)


def is_audio_control(event: BrowserJsonEvent) -> bool:
    return event.event_type == "audio_control" and event.payload.get("command") == "stop"
