from __future__ import annotations

import base64
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar, TypeVar, Union, get_args, get_origin, get_type_hints
from uuid import UUID, uuid4

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
T = TypeVar("T", bound="SerializableDto")


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> UUID:
    return uuid4()


def _serialize_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"__bytes_b64__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value):
        return value.to_dict() if isinstance(value, SerializableDto) else {
            field.name: _serialize_value(getattr(value, field.name)) for field in fields(value)
        }
    if isinstance(value, tuple):
        return [_serialize_value(item) for item in value]
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    return value


def _is_optional(annotation: Any) -> bool:
    return get_origin(annotation) in (Union, types_union()) and type(None) in get_args(annotation)


def types_union() -> Any:
    return type(str | None)


def _inner_optional(annotation: Any) -> Any:
    return next(arg for arg in get_args(annotation) if arg is not type(None))


def _deserialize_value(value: Any, annotation: Any) -> Any:
    if value is None:
        return None
    if _is_optional(annotation):
        return _deserialize_value(value, _inner_optional(annotation))
    origin = get_origin(annotation)
    if annotation is UUID:
        return UUID(str(value))
    if annotation is datetime:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    if annotation is bytes:
        if isinstance(value, dict) and "__bytes_b64__" in value:
            return base64.b64decode(value["__bytes_b64__"])
        return bytes(value)
    if isinstance(annotation, type) and issubclass(annotation, StrEnum):
        return annotation(str(value))
    if origin is tuple:
        args = get_args(annotation)
        item_type = args[0] if args else Any
        return tuple(_deserialize_value(item, item_type) for item in value)
    if origin is list:
        args = get_args(annotation)
        item_type = args[0] if args else Any
        return [_deserialize_value(item, item_type) for item in value]
    if origin is dict:
        return dict(value)
    return value


class SerializableDto:
    json_exclude: ClassVar[frozenset[str]] = frozenset()

    def to_dict(self) -> dict[str, Any]:
        return {
            field.name: _serialize_value(getattr(self, field.name))
            for field in fields(self)
            if field.name not in self.json_exclude
        }

    @classmethod
    def from_dict(cls: type[T], payload: dict[str, Any]) -> T:
        hints = get_type_hints(cls)
        kwargs: dict[str, Any] = {}
        for dto_field in fields(cls):
            if dto_field.name not in payload:
                if dto_field.default is not MISSING or dto_field.default_factory is not MISSING:
                    continue
                raise KeyError(dto_field.name)
            kwargs[dto_field.name] = _deserialize_value(
                payload[dto_field.name],
                hints[dto_field.name],
            )
        return cls(**kwargs)


class FloorState(StrEnum):
    LISTENING = "listening"
    USER_SPEAKING = "user_speaking"
    TOMOKO_SPEAKING = "tomoko_speaking"
    HOLDING = "holding"
    IDLE_GAP = "idle_gap"


class SpeechDecisionKind(StrEnum):
    SILENCE = "silence"
    PREPARE_ONLY = "prepare_only"
    SHORT_REACTION = "short_reaction"
    FULL_REPLY = "full_reply"
    INITIATIVE = "initiative"
    HOLD_FLOOR = "hold_floor"
    YIELD_FLOOR = "yield_floor"
    STOP = "stop"


class CandidateLifecycle(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    SPOKEN = "spoken"
    DISMISSED = "dismissed"


class PromptScope(StrEnum):
    PROVISIONAL = "provisional"
    SHORT = "short"
    MAIN = "main"
    INITIATIVE = "initiative"
    FOLLOW_UP = "follow_up"


class CancelPolicy(StrEnum):
    CANCEL_ON_FINAL_DIVERGENCE = "cancel_on_final_divergence"
    CANCEL_ON_USER_SPEAKING = "cancel_on_user_speaking"
    CANCEL_ON_STOP = "cancel_on_stop"
    KEEP_UNTIL_COMPLETE = "keep_until_complete"


class StopStrength(StrEnum):
    SOFT = "soft"
    NORMAL = "normal"
    HARD = "hard"
    SYSTEM = "system"


class StopArbitration(StrEnum):
    OBEY = "obey"
    ALLOW_ONE_MORE = "allow_one_more"


@dataclass(slots=True)
class AudioSpeechSegment(SerializableDto):
    samples: tuple[float, ...]
    sample_rate: int
    started_at: datetime
    ended_at: datetime
    trace_id: UUID = field(default_factory=new_id)


@dataclass(slots=True)
class PartialTranscriptObservation(SerializableDto):
    text: str
    is_final: bool
    stability: float
    audio_started_at: datetime
    audio_ended_at: datetime
    id: UUID = field(default_factory=new_id)
    p_yielding: float | None = None
    recommended_silence_ms: int | None = None
    source_event_id: UUID | None = None
    trace_id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class FinalTranscriptEvent(SerializableDto):
    text: str
    observation_id: UUID
    audio_started_at: datetime
    audio_ended_at: datetime
    id: UUID = field(default_factory=new_id)
    trace_id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class DurableUtterance(SerializableDto):
    session_id: UUID
    speaker: str
    text: str
    stt_observation_id: UUID | None = None
    id: UUID = field(default_factory=new_id)
    trace_id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class ConversationHistoryItem(SerializableDto):
    speaker: str
    text: str


@dataclass(slots=True)
class FloorSignal(SerializableDto):
    floor_state: FloorState
    silence_ms: int
    user_speaking: bool = False
    tomoko_speaking: bool = False
    playback_active: bool = False
    p_yielding: float | None = None
    candidate_pressure: float = 0.0
    user_present: bool = True
    semantic_saturation: float = 0.0
    stop_requested: bool = False


@dataclass(slots=True)
class FloorObservation(SerializableDto):
    floor_state: FloorState
    silence_ms: int
    user_speaking: bool
    tomoko_speaking: bool
    id: UUID = field(default_factory=new_id)
    p_yielding: float | None = None
    playback_active: bool = False
    trace_id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class SpeechDecision(SerializableDto):
    decision: SpeechDecisionKind
    should_execute: bool
    reason: str
    score_breakdown: dict[str, float]
    id: UUID = field(default_factory=new_id)
    floor_observation_id: UUID | None = None
    prompt_request_id: UUID | None = None
    log_only: bool = False
    trace_id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class UserStatusObservation(SerializableDto):
    present: bool
    activity_label: str
    summary: str
    source: str
    id: UUID = field(default_factory=new_id)
    confidence: float = 0.0
    visible_text: str = ""
    app_name: str | None = None
    window_title: str | None = None
    url: str | None = None
    artifact_path: str | None = None
    trace_id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class CandidateSeed(SerializableDto):
    source: str
    source_key: str
    text: str
    priority: float
    urgency: float
    intrusion: float
    maturity: float
    context_tags: tuple[str, ...]
    id: UUID = field(default_factory=new_id)
    trace_id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class CandidateRecord(SerializableDto):
    seed_id: UUID
    source: str
    source_key: str
    text: str
    priority: float
    urgency: float
    intrusion: float
    maturity: float
    lifecycle: CandidateLifecycle
    context_tags: tuple[str, ...]
    id: UUID = field(default_factory=new_id)
    candidate_score: float = 0.0
    expires_at: datetime | None = None
    spoken_at: datetime | None = None
    trace_id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class SessionSummary(SerializableDto):
    session_id: UUID
    keyword: str
    conclusion: str
    embedding: tuple[float, ...]
    id: UUID = field(default_factory=new_id)
    trace_id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class ContextSnapshot(SerializableDto):
    session_id: UUID | None
    recent_utterances: tuple[str, ...]
    summaries: tuple[SessionSummary, ...]
    calendar_items: dict[str, str]
    user_status: UserStatusObservation | None
    candidates: tuple[CandidateRecord, ...]
    recent_history: tuple[ConversationHistoryItem, ...] = ()
    id: UUID = field(default_factory=new_id)
    elapsed_ms: float = 0.0
    trace_id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class PromptRequest(SerializableDto):
    prompt_text: str
    scope: PromptScope
    decision_id: UUID | None
    utterance_id: UUID | None
    candidate_id: UUID | None
    priority: int
    cancel_policy: CancelPolicy
    id: UUID = field(default_factory=new_id)
    context_snapshot_id: UUID | None = None
    trace_id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class ModelOutputEvent(SerializableDto):
    request_id: UUID
    event_kind: str
    text_delta: str = ""
    text: str = ""
    id: UUID = field(default_factory=new_id)
    discarded: bool = False
    error: str | None = None
    trace_id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class AudioChunkOut(SerializableDto):
    request_id: UUID
    chunk: bytes
    sample_rate: int
    content_type: str = "audio/wav"
    id: UUID = field(default_factory=new_id)
    is_final: bool = False
    trace_id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class EvalTurn(SerializableDto):
    session_id: UUID
    speech_end_to_first_text_ms: float
    speech_end_to_first_audio_ms: float
    turn_total_latency_ms: float
    metrics: dict[str, JsonValue]
    id: UUID = field(default_factory=new_id)
    trace_id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class EvalScore(SerializableDto):
    eval_turn_id: UUID
    responsiveness: float
    attended_feeling: float
    turn_taking_naturalness: float
    interruption_robustness: float
    memory_naturalness: float
    persona_consistency: float
    recovery_quality: float
    id: UUID = field(default_factory=new_id)
    notes: str = ""
    trace_id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)
