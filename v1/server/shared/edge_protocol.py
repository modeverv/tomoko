from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

EdgeEventType = Literal[
    "hello",
    "presence",
    "speech",
    "playback_started",
    "playback_ended",
]


@dataclass(frozen=True)
class EdgeHelloEvent:
    device_id: str
    event_id: UUID = field(default_factory=uuid4)
    sent_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    type: Literal["hello"] = "hello"

    def to_json(self) -> dict[str, Any]:
        return _base_event(self.type, self.event_id, self.device_id, self.sent_at)


@dataclass(frozen=True)
class EdgePresenceEvent:
    device_id: str
    audio_level_db: float
    observed_at: datetime
    is_speaking: bool = True
    speaker: str | None = None
    event_id: UUID = field(default_factory=uuid4)
    sent_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    type: Literal["presence"] = "presence"

    def to_json(self) -> dict[str, Any]:
        payload = _base_event(self.type, self.event_id, self.device_id, self.sent_at)
        payload.update(
            {
                "audio_level_db": self.audio_level_db,
                "observed_at": _format_datetime(self.observed_at),
                "is_speaking": self.is_speaking,
            }
        )
        if self.speaker is not None:
            payload["speaker"] = self.speaker
        return payload


@dataclass(frozen=True)
class EdgeSpeechEvent:
    device_id: str
    transcript: str
    audio_level_db: float
    observed_at: datetime
    transcript_id: UUID = field(default_factory=uuid4)
    speaker: str | None = None
    event_id: UUID = field(default_factory=uuid4)
    sent_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    type: Literal["speech"] = "speech"

    def to_json(self) -> dict[str, Any]:
        payload = _base_event(self.type, self.event_id, self.device_id, self.sent_at)
        payload.update(
            {
                "transcript_id": str(self.transcript_id),
                "transcript": self.transcript,
                "audio_level_db": self.audio_level_db,
                "observed_at": _format_datetime(self.observed_at),
            }
        )
        if self.speaker is not None:
            payload["speaker"] = self.speaker
        return payload


@dataclass(frozen=True)
class EdgePlaybackTelemetryEvent:
    type: Literal["playback_started", "playback_ended"]
    device_id: str
    turn_id: str | None
    chunk_id: int | None = None
    scheduled_audio_time: float | None = None
    sent_audio_time: float | None = None
    audio_context_time: float | None = None
    performance_now_ms: float | None = None
    event_id: UUID = field(default_factory=uuid4)
    sent_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_json(self) -> dict[str, Any]:
        payload = _base_event(self.type, self.event_id, self.device_id, self.sent_at)
        payload.update(
            {
                "turn_id": self.turn_id,
                "chunk_id": self.chunk_id,
                "scheduled_audio_time": self.scheduled_audio_time,
                "sent_audio_time": self.sent_audio_time,
                "audio_context_time": self.audio_context_time,
                "performance_now_ms": self.performance_now_ms,
            }
        )
        return {key: value for key, value in payload.items() if value is not None}


def parse_edge_event(payload: dict[str, Any]) -> (
    EdgeHelloEvent
    | EdgePresenceEvent
    | EdgeSpeechEvent
    | EdgePlaybackTelemetryEvent
):
    event_type = payload.get("type")
    device_id = _required_str(payload, "device_id")
    event_id = _optional_uuid(payload.get("event_id")) or uuid4()
    sent_at = _optional_datetime(payload.get("sent_at")) or datetime.now(UTC)
    if event_type == "hello":
        return EdgeHelloEvent(device_id=device_id, event_id=event_id, sent_at=sent_at)
    if event_type == "presence":
        return EdgePresenceEvent(
            device_id=device_id,
            audio_level_db=float(payload.get("audio_level_db", -120.0)),
            observed_at=_required_datetime(payload, "observed_at"),
            is_speaking=bool(payload.get("is_speaking", True)),
            speaker=_optional_str(payload.get("speaker")),
            event_id=event_id,
            sent_at=sent_at,
        )
    if event_type == "speech":
        return EdgeSpeechEvent(
            device_id=device_id,
            transcript=_required_str(payload, "transcript"),
            audio_level_db=float(payload.get("audio_level_db", -120.0)),
            observed_at=_required_datetime(payload, "observed_at"),
            transcript_id=_optional_uuid(payload.get("transcript_id")) or uuid4(),
            speaker=_optional_str(payload.get("speaker")),
            event_id=event_id,
            sent_at=sent_at,
        )
    if event_type in {"playback_started", "playback_ended"}:
        return EdgePlaybackTelemetryEvent(
            type=event_type,
            device_id=device_id,
            turn_id=_optional_str(payload.get("turn_id")),
            chunk_id=_optional_int(payload.get("chunk_id")),
            scheduled_audio_time=_optional_float(payload.get("scheduled_audio_time")),
            sent_audio_time=_optional_float(payload.get("sent_audio_time")),
            audio_context_time=_optional_float(payload.get("audio_context_time")),
            performance_now_ms=_optional_float(payload.get("performance_now_ms")),
            event_id=event_id,
            sent_at=sent_at,
        )
    raise ValueError(f"unsupported edge event type: {event_type}")


def _base_event(
    event_type: str,
    event_id: UUID,
    device_id: str,
    sent_at: datetime,
) -> dict[str, Any]:
    if not device_id:
        raise ValueError("edge protocol device_id must not be empty")
    return {
        "type": event_type,
        "event_id": str(event_id),
        "device_id": device_id,
        "sent_at": _format_datetime(sent_at),
    }


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing edge protocol field: {key}")
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _required_datetime(payload: dict[str, Any], key: str) -> datetime:
    value = _optional_datetime(payload.get(key))
    if value is None:
        raise ValueError(f"missing edge protocol datetime field: {key}")
    return value


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None


def _format_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _optional_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


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
