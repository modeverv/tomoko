from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from server.shared.models import PlaybackTelemetry, SessionEvent


def playback_telemetry_from_event(event: SessionEvent) -> PlaybackTelemetry:
    if event.type not in {"playback_started", "playback_ended"}:
        raise ValueError(f"not a playback event: {event.type}")
    return PlaybackTelemetry(
        type=event.type,  # type: ignore[arg-type]
        turn_id=optional_str_payload(event.payload.get("turn_id")),
        chunk_id=optional_int_payload(event.payload.get("chunk_id")),
        scheduled_audio_time=optional_float_payload(
            event.payload.get("scheduled_audio_time")
        ),
        sent_audio_time=optional_float_payload(event.payload.get("sent_audio_time")),
        audio_context_time=optional_float_payload(event.payload.get("audio_context_time")),
        performance_now_ms=optional_float_payload(
            event.payload.get("performance_now_ms")
        ),
    )


def playback_payload(event: SessionEvent) -> dict[str, Any]:
    return {
        "turn_id": event.payload.get("turn_id"),
        "chunk_id": event.payload.get("chunk_id"),
    }


def json_safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: json_safe_value(value) for key, value in payload.items()}


def json_safe_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe_value(item) for item in value]
    return value


def optional_str_payload(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def optional_int_payload(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def optional_float_payload(value: object) -> float | None:
    if value is None:
        return None
    return float(value)
