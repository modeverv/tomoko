from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import numpy as np

AttentionMode = Literal["ambient", "engaged", "cooldown", "withdrawn"]
ParticipationMode = Literal["called", "invited", "observer", "withdraw"]
StartReason = Literal[
    "wake_word",
    "followup",
    "initiative",
    "arrival",
    "resume_unspoken",
]
BargeInKind = Literal[
    "echo",
    "backchannel",
    "soft_interrupt",
    "hard_interrupt",
    "new_question",
]
BargeInAction = Literal["continue_speaking", "finish_sentence", "restart_turn"]
BackchannelSuggestionKind = Literal[
    "react",
    "emo",
    "understood",
    "floor_take",
]
PlaybackEventType = Literal["playback_started", "playback_ended"]
OutputLane = Literal[
    "reply_turn",
    "initiative_turn",
    "gesture_audio",
    "stop_ack",
    "interrupting_turn",
]
TranscriptFilterAction = Literal["accept", "suppress_partial", "drop"]
ConversationLogStatus = Literal["completed", "interrupted", "cancelled", "error"]
SummaryStatus = Literal["not_ready", "pending", "processing", "completed", "error"]
PersonaVersionStatus = Literal["completed", "error"]
ContextDepth = Literal["fast", "normal", "deep", "reflective"]
VadState = Literal["idle", "listening", "processing"]
PlaybackState = Literal["idle", "speaking", "client_playing", "echo_grace"]
ConnectionRole = Literal["browser", "edge", "monitor"]
CandidateSpeakDecisionKind = Literal["speak", "wait", "needs_llm_judge"]
LLMJudgeDecisionKind = Literal["speak_now", "wait", "defer"]
TurnTakingPendingReplyState = Literal[
    "none",
    "generating_not_started",
    "text_started",
    "audio_started",
]
TurnTakingDecisionKind = Literal[
    "ignore_as_noise",
    "continue_current_reply",
    "defer_output",
    "restart_with_new_input",
    "stop_speaking",
]
WorldObservationDocumentStatus = Literal[
    "pending",
    "normalizing",
    "completed",
    "failed",
]
WorldObservationFreshness = Literal["breaking", "fresh", "recent", "stale", "unknown"]
WorldObservationEmotionalTone = Literal[
    "neutral",
    "hopeful",
    "concerned",
    "curious",
    "playful",
    "sad",
]


@dataclass
class SpeechSegment:
    audio: np.ndarray
    started_at: datetime
    ended_at: datetime
    device_id: str
    vad_confidence: float


@dataclass
class Transcript:
    text: str
    device_id: str
    speaker: str | None
    audio_level_db: float
    recorded_at: datetime
    is_final: bool


@dataclass
class ParticipationDecision:
    should_participate: bool
    mode: ParticipationMode
    reason: str


@dataclass(frozen=True)
class BargeInContext:
    transcript: str
    recent_tomoko_text: str
    speaking_elapsed_ms: float


@dataclass(frozen=True)
class BargeInDecision:
    kind: BargeInKind
    action: BargeInAction
    reason: str


@dataclass(frozen=True)
class BackchannelSuggestion:
    kind: BackchannelSuggestionKind
    score: float
    source: str
    observed_at: datetime
    reason: str = ""
    schema_version: int = 1

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> BackchannelSuggestion:
        kind = str(payload.get("kind", "react"))
        if kind not in {"react", "emo", "understood", "floor_take"}:
            kind = "react"
        observed_at = payload.get("observed_at")
        if isinstance(observed_at, datetime):
            parsed_observed_at = observed_at
        elif isinstance(observed_at, str):
            parsed_observed_at = datetime.fromisoformat(observed_at)
        else:
            parsed_observed_at = datetime.now(UTC)
        return cls(
            schema_version=int(payload.get("schema_version", 1)),
            kind=kind,  # type: ignore[arg-type]
            score=_float_or_zero(payload.get("score")),
            source=str(payload.get("source", "unknown"))[:40],
            observed_at=parsed_observed_at,
            reason=str(payload.get("reason", ""))[:120],
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "score": self.score,
            "source": self.source,
            "observed_at": self.observed_at.isoformat(),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PlaybackTelemetry:
    type: PlaybackEventType
    turn_id: str | None
    chunk_id: int | None = None
    scheduled_audio_time: float | None = None
    sent_audio_time: float | None = None
    audio_context_time: float | None = None
    performance_now_ms: float | None = None


@dataclass(frozen=True)
class ClientConnection:
    connection_id: str
    device_id: str
    role: ConnectionRole
    can_receive_audio: bool
    can_receive_display: bool
    connected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_seen_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class ConnectedOutputState:
    active_device_id: str | None = None
    audio_target_available: bool = False
    display_target_available: bool = False
    connected_device_count: int = 0
    connected_connection_count: int = 0
    playback_state_by_device: dict[str, PlaybackState] = field(default_factory=dict)
    last_presence_at: datetime | None = None

    @classmethod
    def empty(cls) -> ConnectedOutputState:
        return cls()

    @classmethod
    def single_client(
        cls,
        *,
        device_id: str,
        can_receive_audio: bool = True,
        can_receive_display: bool = True,
        last_presence_at: datetime | None = None,
    ) -> ConnectedOutputState:
        return cls(
            active_device_id=device_id,
            audio_target_available=can_receive_audio,
            display_target_available=can_receive_display,
            connected_device_count=1,
            connected_connection_count=1,
            last_presence_at=last_presence_at or datetime.now(UTC),
        )


@dataclass(frozen=True)
class TurnTakingAudioMetrics:
    segment_ms: float
    rms_db: float
    peak_db: float
    active_frame_ratio: float

    @classmethod
    def unknown(cls, *, audio_level_db: float) -> TurnTakingAudioMetrics:
        return cls(
            segment_ms=0.0,
            rms_db=audio_level_db,
            peak_db=audio_level_db,
            active_frame_ratio=0.0,
        )

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> TurnTakingAudioMetrics:
        return cls(
            segment_ms=_float_or_zero(payload.get("segment_ms")),
            rms_db=_float_or_zero(payload.get("rms_db")),
            peak_db=_float_or_zero(payload.get("peak_db")),
            active_frame_ratio=_float_or_zero(payload.get("active_frame_ratio")),
        )

    def to_json(self) -> dict[str, float]:
        return {
            "segment_ms": self.segment_ms,
            "rms_db": self.rms_db,
            "peak_db": self.peak_db,
            "active_frame_ratio": self.active_frame_ratio,
        }


@dataclass(frozen=True)
class TurnTakingInput:
    pending_reply_state: TurnTakingPendingReplyState
    new_transcript: str
    audio_metrics: TurnTakingAudioMetrics
    attention_mode: AttentionMode
    playback_state: PlaybackState
    recent_turns: tuple[ConversationTurn, ...] = field(default_factory=tuple)
    recent_tomoko_text: str = ""
    schema_version: int = 1

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> TurnTakingInput:
        pending_reply_state = str(payload.get("pending_reply_state", "none"))
        if pending_reply_state not in {
            "none",
            "generating_not_started",
            "text_started",
            "audio_started",
        }:
            pending_reply_state = "none"
        attention_mode = str(payload.get("attention_mode", "ambient"))
        if attention_mode not in {"ambient", "engaged", "cooldown", "withdrawn"}:
            attention_mode = "ambient"
        playback_state = str(payload.get("playback_state", "idle"))
        if playback_state not in {"idle", "speaking", "client_playing", "echo_grace"}:
            playback_state = "idle"
        return cls(
            schema_version=int(payload.get("schema_version", 1)),
            pending_reply_state=pending_reply_state,  # type: ignore[arg-type]
            new_transcript=str(payload.get("new_transcript", "")),
            audio_metrics=TurnTakingAudioMetrics.from_json(
                dict(payload.get("audio_metrics") or {})
            ),
            attention_mode=attention_mode,  # type: ignore[arg-type]
            playback_state=playback_state,  # type: ignore[arg-type]
            recent_tomoko_text=str(payload.get("recent_tomoko_text", "")),
            recent_turns=tuple(),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "pending_reply_state": self.pending_reply_state,
            "new_transcript": self.new_transcript,
            "audio_metrics": self.audio_metrics.to_json(),
            "attention_mode": self.attention_mode,
            "playback_state": self.playback_state,
            "recent_tomoko_text": self.recent_tomoko_text,
            "recent_turns": [
                {
                    "user_text": turn.user_text,
                    "tomoko_text": turn.tomoko_text,
                    "emotion": turn.emotion,
                }
                for turn in self.recent_turns
            ],
        }


@dataclass(frozen=True)
class TurnTakingDecision:
    decision: TurnTakingDecisionKind
    reason: str
    source: str = "rule"
    elapsed_ms: float = 0.0

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> TurnTakingDecision:
        decision = str(payload.get("decision", "continue_current_reply"))
        if decision not in {
            "ignore_as_noise",
            "continue_current_reply",
            "defer_output",
            "restart_with_new_input",
            "stop_speaking",
        }:
            decision = "continue_current_reply"
        return cls(
            decision=decision,  # type: ignore[arg-type]
            reason=str(payload.get("reason", "invalid_or_missing_decision"))[:120],
            source=str(payload.get("source", "worker"))[:40],
            elapsed_ms=_float_or_zero(payload.get("elapsed_ms")),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason": self.reason[:120],
            "source": self.source,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass(frozen=True)
class TomoroRuntimeState:
    attention_mode: AttentionMode
    vad_state: VadState
    playback_state: PlaybackState
    active_session_id: UUID | None
    active_turn_id: str | None
    speaking_turn_id: str | None
    context_build_id: UUID | None
    last_start_reason: StartReason | None = None
    output_state: ConnectedOutputState = field(default_factory=ConnectedOutputState.empty)
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class TomokoDesireState:
    desire_1m: float = 0.0
    desire_5m: float = 0.0
    desire_30m: float = 0.0
    unspoken_pressure: float = 0.0
    curiosity_pressure: float = 0.0
    attachment_pressure: float = 0.0
    playful_pressure: float = 0.0
    schema_version: int = 1

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> TomokoDesireState:
        schema_version = int(payload.get("schema_version", 1))
        if schema_version != 1:
            raise ValueError(
                f"Unsupported TomokoDesireState schema_version: {schema_version}"
            )
        return cls(
            schema_version=schema_version,
            desire_1m=_float_or_zero(payload.get("desire_1m")),
            desire_5m=_float_or_zero(payload.get("desire_5m")),
            desire_30m=_float_or_zero(payload.get("desire_30m")),
            unspoken_pressure=_float_or_zero(payload.get("unspoken_pressure")),
            curiosity_pressure=_float_or_zero(payload.get("curiosity_pressure")),
            attachment_pressure=_float_or_zero(payload.get("attachment_pressure")),
            playful_pressure=_float_or_zero(payload.get("playful_pressure")),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "desire_1m": self.desire_1m,
            "desire_5m": self.desire_5m,
            "desire_30m": self.desire_30m,
            "unspoken_pressure": self.unspoken_pressure,
            "curiosity_pressure": self.curiosity_pressure,
            "attachment_pressure": self.attachment_pressure,
            "playful_pressure": self.playful_pressure,
        }


@dataclass(frozen=True)
class SpeakabilityState:
    presence_1m: float = 0.0
    presence_5m: float = 0.0
    activity_1m: float = 0.0
    activity_5m: float = 0.0
    conversation_heat_1m: float = 0.0
    conversation_heat_5m: float = 0.0
    focus_likelihood_5m: float = 0.0
    recent_rejection_score: float = 0.0
    recent_acceptance_score: float = 0.0
    intrusion_penalty: float = 0.0
    schema_version: int = 1

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> SpeakabilityState:
        schema_version = int(payload.get("schema_version", 1))
        if schema_version != 1:
            raise ValueError(
                f"Unsupported SpeakabilityState schema_version: {schema_version}"
            )
        return cls(
            schema_version=schema_version,
            presence_1m=_float_or_zero(payload.get("presence_1m")),
            presence_5m=_float_or_zero(payload.get("presence_5m")),
            activity_1m=_float_or_zero(payload.get("activity_1m")),
            activity_5m=_float_or_zero(payload.get("activity_5m")),
            conversation_heat_1m=_float_or_zero(payload.get("conversation_heat_1m")),
            conversation_heat_5m=_float_or_zero(payload.get("conversation_heat_5m")),
            focus_likelihood_5m=_float_or_zero(payload.get("focus_likelihood_5m")),
            recent_rejection_score=_float_or_zero(
                payload.get("recent_rejection_score")
            ),
            recent_acceptance_score=_float_or_zero(
                payload.get("recent_acceptance_score")
            ),
            intrusion_penalty=_float_or_zero(payload.get("intrusion_penalty")),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "presence_1m": self.presence_1m,
            "presence_5m": self.presence_5m,
            "activity_1m": self.activity_1m,
            "activity_5m": self.activity_5m,
            "conversation_heat_1m": self.conversation_heat_1m,
            "conversation_heat_5m": self.conversation_heat_5m,
            "focus_likelihood_5m": self.focus_likelihood_5m,
            "recent_rejection_score": self.recent_rejection_score,
            "recent_acceptance_score": self.recent_acceptance_score,
            "intrusion_penalty": self.intrusion_penalty,
        }


@dataclass(frozen=True)
class PersonalityDynamics:
    talkativeness: float = 0.5
    restraint: float = 0.5
    curiosity: float = 0.5
    attachment: float = 0.5
    sensitivity: float = 0.5
    playfulness: float = 0.5
    mood_talkativeness_1h: float = 0.0
    mood_restraint_1h: float = 0.0
    mood_curiosity_1h: float = 0.0
    schema_version: int = 1

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> PersonalityDynamics:
        schema_version = int(payload.get("schema_version", 1))
        if schema_version != 1:
            raise ValueError(
                f"Unsupported PersonalityDynamics schema_version: {schema_version}"
            )
        return cls(
            schema_version=schema_version,
            talkativeness=float(payload.get("talkativeness", 0.5)),
            restraint=float(payload.get("restraint", 0.5)),
            curiosity=float(payload.get("curiosity", 0.5)),
            attachment=float(payload.get("attachment", 0.5)),
            sensitivity=float(payload.get("sensitivity", 0.5)),
            playfulness=float(payload.get("playfulness", 0.5)),
            mood_talkativeness_1h=_float_or_zero(
                payload.get("mood_talkativeness_1h")
            ),
            mood_restraint_1h=_float_or_zero(payload.get("mood_restraint_1h")),
            mood_curiosity_1h=_float_or_zero(payload.get("mood_curiosity_1h")),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "talkativeness": self.talkativeness,
            "restraint": self.restraint,
            "curiosity": self.curiosity,
            "attachment": self.attachment,
            "sensitivity": self.sensitivity,
            "playfulness": self.playfulness,
            "mood_talkativeness_1h": self.mood_talkativeness_1h,
            "mood_restraint_1h": self.mood_restraint_1h,
            "mood_curiosity_1h": self.mood_curiosity_1h,
        }


@dataclass(frozen=True)
class CandidateSpeakMetadata:
    candidate_id: UUID | None = None
    source: str = ""
    priority: float = 0.0
    urgency: float = 0.0
    intrusion_risk: float = 0.0
    emotional_need: float = 0.0
    feedback_penalty: float = 0.0
    feedback_boost: float = 0.0
    maturity: int = 0
    text_ready: bool = False
    audio_ready: bool = False
    expires_at: datetime | None = None
    context_tags: tuple[str, ...] = field(default_factory=tuple)
    reason: str | None = None
    generated_text: str | None = None
    schema_version: int = 1

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> CandidateSpeakMetadata:
        schema_version = int(payload.get("schema_version", 1))
        if schema_version != 1:
            raise ValueError(
                f"Unsupported CandidateSpeakMetadata schema_version: {schema_version}"
            )
        return cls(
            schema_version=schema_version,
            candidate_id=_optional_uuid(payload.get("candidate_id")),
            source=str(payload.get("source", "")),
            priority=_float_or_zero(payload.get("priority")),
            urgency=_float_or_zero(payload.get("urgency")),
            intrusion_risk=_float_or_zero(payload.get("intrusion_risk")),
            emotional_need=_float_or_zero(payload.get("emotional_need")),
            feedback_penalty=_float_or_zero(payload.get("feedback_penalty")),
            feedback_boost=_float_or_zero(payload.get("feedback_boost")),
            maturity=int(payload.get("maturity", 0)),
            text_ready=bool(payload.get("text_ready", False)),
            audio_ready=bool(payload.get("audio_ready", False)),
            expires_at=_optional_datetime_value(payload.get("expires_at")),
            context_tags=tuple(str(tag) for tag in payload.get("context_tags", ())),
            reason=_optional_str(payload.get("reason")),
            generated_text=_optional_str(payload.get("generated_text")),
        )

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "source": self.source,
            "priority": self.priority,
            "urgency": self.urgency,
            "intrusion_risk": self.intrusion_risk,
            "emotional_need": self.emotional_need,
            "feedback_penalty": self.feedback_penalty,
            "feedback_boost": self.feedback_boost,
            "maturity": self.maturity,
            "text_ready": self.text_ready,
            "audio_ready": self.audio_ready,
            "context_tags": list(self.context_tags),
        }
        if self.candidate_id is not None:
            payload["candidate_id"] = str(self.candidate_id)
        if self.expires_at is not None:
            payload["expires_at"] = self.expires_at.isoformat()
        if self.reason is not None:
            payload["reason"] = self.reason
        if self.generated_text is not None:
            payload["generated_text"] = self.generated_text
        return payload


@dataclass(frozen=True)
class CandidateFeedbackScope:
    source: str
    topic: str | None = None
    emotional_need: str | None = None
    candidate_id: UUID | None = None
    schema_version: int = 1

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> CandidateFeedbackScope:
        schema_version = int(payload.get("schema_version", 1))
        if schema_version != 1:
            raise ValueError(
                f"Unsupported CandidateFeedbackScope schema_version: {schema_version}"
            )
        return cls(
            schema_version=schema_version,
            source=str(payload.get("source", "")),
            topic=_optional_str(payload.get("topic")),
            emotional_need=_optional_str(payload.get("emotional_need")),
            candidate_id=_optional_uuid(payload.get("candidate_id")),
        )

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "source": self.source,
        }
        if self.topic is not None:
            payload["topic"] = self.topic
        if self.emotional_need is not None:
            payload["emotional_need"] = self.emotional_need
        if self.candidate_id is not None:
            payload["candidate_id"] = str(self.candidate_id)
        return payload


@dataclass(frozen=True)
class CandidateFeedbackSummary:
    rejection_score: float = 0.0
    acceptance_score: float = 0.0
    intrusion_penalty: float = 0.0
    feedback_penalty: float = 0.0
    feedback_boost: float = 0.0
    schema_version: int = 1

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> CandidateFeedbackSummary:
        schema_version = int(payload.get("schema_version", 1))
        if schema_version != 1:
            raise ValueError(
                f"Unsupported CandidateFeedbackSummary schema_version: {schema_version}"
            )
        return cls(
            schema_version=schema_version,
            rejection_score=_float_or_zero(payload.get("rejection_score")),
            acceptance_score=_float_or_zero(payload.get("acceptance_score")),
            intrusion_penalty=_float_or_zero(payload.get("intrusion_penalty")),
            feedback_penalty=_float_or_zero(payload.get("feedback_penalty")),
            feedback_boost=_float_or_zero(payload.get("feedback_boost")),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "rejection_score": self.rejection_score,
            "acceptance_score": self.acceptance_score,
            "intrusion_penalty": self.intrusion_penalty,
            "feedback_penalty": self.feedback_penalty,
            "feedback_boost": self.feedback_boost,
        }


@dataclass(frozen=True)
class WorldObservationParseIssue:
    field: str
    message: str
    severity: Literal["warning", "error"] = "error"

    def to_json(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "message": self.message,
            "severity": self.severity,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> WorldObservationParseIssue:
        severity = str(payload.get("severity", "error"))
        if severity not in {"warning", "error"}:
            severity = "error"
        return cls(
            field=str(payload.get("field", "")),
            message=str(payload.get("message", "")),
            severity=severity,  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class WorldObservationRawMetadata:
    schema_version: int
    kind: str
    generated_by: str
    observed_at: datetime
    language: str
    topics: tuple[str, ...]
    source_policy: str
    collection_prompt_version: str

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "generated_by": self.generated_by,
            "observed_at": self.observed_at.isoformat(),
            "language": self.language,
            "topics": list(self.topics),
            "source_policy": self.source_policy,
            "collection_prompt_version": self.collection_prompt_version,
        }


@dataclass(frozen=True)
class WorldObservationRawDocument:
    path: str
    metadata: WorldObservationRawMetadata | None
    body: str
    raw_frontmatter: dict[str, Any]
    issues: tuple[WorldObservationParseIssue, ...] = field(default_factory=tuple)

    @property
    def is_valid(self) -> bool:
        return self.metadata is not None and not any(
            issue.severity == "error" for issue in self.issues
        )


@dataclass(frozen=True)
class WorldObservationNormalizeTrace:
    model: str
    elapsed_ms: float
    attempts: int
    issues: tuple[WorldObservationParseIssue, ...] = field(default_factory=tuple)

    def to_json(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "elapsed_ms": self.elapsed_ms,
            "attempts": self.attempts,
            "issues": [issue.to_json() for issue in self.issues],
        }


@dataclass(frozen=True)
class WorldObservationNormalizedItem:
    topic: str
    title: str
    summary: str
    source_hint: str
    freshness: WorldObservationFreshness
    confidence: float
    raw_excerpt: str
    item_json: dict[str, Any] = field(default_factory=dict)
    parse_notes: tuple[str, ...] = field(default_factory=tuple)
    schema_version: int = 1

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> WorldObservationNormalizedItem:
        freshness = str(payload.get("freshness", "unknown"))
        if freshness not in {"breaking", "fresh", "recent", "stale", "unknown"}:
            freshness = "unknown"
        return cls(
            schema_version=int(payload.get("schema_version", 1)),
            topic=str(payload.get("topic", "")).strip(),
            title=str(payload.get("title", "")).strip(),
            summary=str(payload.get("summary", "")).strip(),
            source_hint=str(payload.get("source_hint", "")).strip(),
            freshness=freshness,  # type: ignore[arg-type]
            confidence=_clamp_float(payload.get("confidence"), minimum=0.0, maximum=1.0),
            raw_excerpt=str(payload.get("raw_excerpt", "")).strip(),
            item_json=dict(payload.get("item_json") or {}),
            parse_notes=tuple(str(item) for item in payload.get("parse_notes", ())),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "topic": self.topic,
            "title": self.title,
            "summary": self.summary,
            "source_hint": self.source_hint,
            "freshness": self.freshness,
            "confidence": self.confidence,
            "raw_excerpt": self.raw_excerpt,
            "item_json": dict(self.item_json),
            "parse_notes": list(self.parse_notes),
        }


@dataclass(frozen=True)
class WorldObservationNormalizedBatch:
    items: tuple[WorldObservationNormalizedItem, ...]
    trace: WorldObservationNormalizeTrace
    schema_version: int = 1


@dataclass(frozen=True)
class WorldObservationDocumentRecord:
    id: UUID
    raw_file_path: str
    sha256_checksum: str
    generated_by: str
    observed_at: datetime
    imported_at: datetime
    status: WorldObservationDocumentStatus
    metadata_json: dict[str, Any]
    parse_issues_json: list[dict[str, Any]]


@dataclass(frozen=True)
class WorldObservationItemRecord:
    id: UUID
    document_id: UUID
    topic: str
    title: str
    summary: str
    source_hint: str
    freshness: WorldObservationFreshness
    confidence: float
    item_json: dict[str, Any]
    raw_excerpt: str
    created_at: datetime


@dataclass(frozen=True)
class WorldObservationInterpretation:
    item_id: UUID
    relevance_to_user: float
    tomoko_interest: float
    emotional_tone: WorldObservationEmotionalTone
    memory_value: float
    speakability_hint: str
    interpretation_text: str
    tomoko_private_reaction: str = ""
    candidate_seed_text: str = ""
    reason_json: dict[str, Any] = field(default_factory=dict)
    persona_state_version_id: UUID | None = None
    persona_lexicon_version_id: UUID | None = None
    schema_version: int = 1

    @classmethod
    def from_json(
        cls,
        payload: dict[str, Any],
        *,
        item_id: UUID,
        persona_state_version_id: UUID | None = None,
        persona_lexicon_version_id: UUID | None = None,
    ) -> WorldObservationInterpretation:
        tone = str(payload.get("emotional_tone", "neutral"))
        if tone not in {"neutral", "hopeful", "concerned", "curious", "playful", "sad"}:
            tone = "neutral"
        speakability_hint = str(payload.get("speakability_hint", "avoid")).strip()
        if speakability_hint not in {"short_now", "later", "diary", "avoid"}:
            raise ValueError("speakability_hint must be short_now, later, diary, or avoid")
        reason_json = dict(payload.get("reason_json") or {})
        required_reason_keys = {
            "persona_basis",
            "user_basis",
            "speakability_basis",
            "avoid_overclaim",
        }
        missing_reason_keys = [
            key for key in sorted(required_reason_keys) if not str(reason_json.get(key, "")).strip()
        ]
        if missing_reason_keys:
            raise ValueError(
                "reason_json is missing required keys: "
                + ", ".join(missing_reason_keys)
            )
        return cls(
            schema_version=int(payload.get("schema_version", 1)),
            item_id=item_id,
            persona_state_version_id=persona_state_version_id,
            persona_lexicon_version_id=persona_lexicon_version_id,
            relevance_to_user=_clamp_float(
                payload.get("relevance_to_user"),
                minimum=0.0,
                maximum=1.0,
            ),
            tomoko_interest=_clamp_float(
                payload.get("tomoko_interest"),
                minimum=0.0,
                maximum=1.0,
            ),
            emotional_tone=tone,  # type: ignore[arg-type]
            memory_value=_clamp_float(
                payload.get("memory_value"),
                minimum=0.0,
                maximum=1.0,
            ),
            speakability_hint=speakability_hint,
            interpretation_text=str(payload.get("interpretation_text", "")).strip(),
            tomoko_private_reaction=str(
                payload.get("tomoko_private_reaction", "")
            ).strip(),
            candidate_seed_text=str(payload.get("candidate_seed_text", "")).strip(),
            reason_json=reason_json,
        )

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "item_id": str(self.item_id),
            "relevance_to_user": self.relevance_to_user,
            "tomoko_interest": self.tomoko_interest,
            "emotional_tone": self.emotional_tone,
            "memory_value": self.memory_value,
            "speakability_hint": self.speakability_hint,
            "interpretation_text": self.interpretation_text,
            "tomoko_private_reaction": self.tomoko_private_reaction,
            "candidate_seed_text": self.candidate_seed_text,
            "reason_json": dict(self.reason_json),
        }
        if self.persona_state_version_id is not None:
            payload["persona_state_version_id"] = str(self.persona_state_version_id)
        if self.persona_lexicon_version_id is not None:
            payload["persona_lexicon_version_id"] = str(
                self.persona_lexicon_version_id
            )
        return payload


@dataclass(frozen=True)
class WorldObservationInterpretationRecord:
    id: UUID
    item_id: UUID
    document_id: UUID
    topic: str
    title: str
    summary: str
    source_hint: str
    freshness: WorldObservationFreshness
    confidence: float
    persona_state_version_id: UUID | None
    persona_lexicon_version_id: UUID | None
    relevance_to_user: float
    tomoko_interest: float
    emotional_tone: WorldObservationEmotionalTone
    memory_value: float
    speakability_hint: str
    interpretation_text: str
    tomoko_private_reaction: str
    candidate_seed_text: str
    reason_json: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class CandidateSpeakDecision:
    decision: CandidateSpeakDecisionKind
    score: float
    threshold: float
    reason: str
    signals: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> CandidateSpeakDecision:
        schema_version = int(payload.get("schema_version", 1))
        if schema_version != 1:
            raise ValueError(
                f"Unsupported CandidateSpeakDecision schema_version: {schema_version}"
            )
        decision = str(payload.get("decision", "wait"))
        if decision not in {"speak", "wait", "needs_llm_judge"}:
            decision = "wait"
        return cls(
            schema_version=schema_version,
            decision=decision,  # type: ignore[arg-type]
            score=_float_or_zero(payload.get("score")),
            threshold=_float_or_zero(payload.get("threshold")),
            reason=str(payload.get("reason", "")),
            signals=dict(payload.get("signals", {})),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "decision": self.decision,
            "score": self.score,
            "threshold": self.threshold,
            "reason": self.reason,
            "signals": dict(self.signals),
        }


@dataclass(frozen=True)
class SessionEvent:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class StateEmission:
    type: str
    payload: dict[str, Any]
    state_snapshot: TomoroRuntimeState
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class SessionCommand:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TransitionResult:
    state: TomoroRuntimeState
    emissions: list[StateEmission] = field(default_factory=list)
    commands: list[SessionCommand] = field(default_factory=list)


@dataclass(frozen=True)
class TranscriptFilterDecision:
    action: TranscriptFilterAction
    reason: str


@dataclass(frozen=True)
class ParticipationContext:
    transcript: str
    attention_mode: AttentionMode = "ambient"
    device_id: str | None = None
    speaker: str | None = None
    audio_level_db: float | None = None

    @classmethod
    def from_transcript(
        cls,
        transcript: Transcript,
        *,
        attention_mode: AttentionMode,
    ) -> ParticipationContext:
        return cls(
            transcript=transcript.text,
            attention_mode=attention_mode,
            device_id=transcript.device_id,
            speaker=transcript.speaker,
            audio_level_db=transcript.audio_level_db,
        )


@dataclass
class ConversationTurn:
    speaker: Literal["user", "tomoko"]
    text: str
    timestamp: datetime
    emotion: str | None = None


@dataclass(frozen=True)
class MemoryHit:
    speaker: Literal["user", "tomoko"]
    text: str
    timestamp: datetime
    similarity: float
    emotion: str | None = None
    source_id: str | None = None


@dataclass(frozen=True)
class ShortMemoryNote:
    kind: Literal["working_context", "short_intent", "next_trial", "verbatim"]
    text: str
    confidence: float
    importance: float
    created_turn: int
    expires_after_turns: int
    created_at: datetime
    note_id: str | None = None


@dataclass(frozen=True)
class ShortMemoryProposalResult:
    proposals: list[ShortMemoryNote]
    decision: Literal["store", "skip"] = "store"
    reason: str | None = None
    raw_text: str | None = None
    source: Literal["heuristic", "llm", "heuristic_fallback"] = "heuristic"


@dataclass(frozen=True)
class SessionSummaryHit:
    session_id: UUID
    summary_text: str
    started_at: datetime
    ended_at: datetime | None
    similarity: float


@dataclass(frozen=True)
class ResearchContextHit:
    result_id: str
    query: str
    summary_text: str
    provider: str
    fetched_at: datetime
    similarity: float
    citation_urls: tuple[str, ...] = ()
    raw_artifact_path: str | None = None


@dataclass(frozen=True)
class TaskLedgerEntry:
    task_id: str
    title: str
    status: Literal["active", "completed", "cancelled", "blocked"]
    priority: int
    created_at: datetime
    updated_at: datetime
    due_at: datetime | None = None
    source: str = "unknown"
    details: str = ""
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class PersonaLexiconTerm:
    term: str
    meaning: str
    salience: float
    tone: str | None = None
    first_seen_session_id: UUID | None = None
    last_seen_session_id: UUID | None = None
    evidence: list[str] = field(default_factory=list)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> PersonaLexiconTerm:
        return cls(
            term=str(payload.get("term", "")),
            meaning=str(payload.get("meaning", "")),
            tone=_optional_str(payload.get("tone")),
            salience=float(payload.get("salience", 0.0)),
            first_seen_session_id=_optional_uuid(payload.get("first_seen_session_id")),
            last_seen_session_id=_optional_uuid(payload.get("last_seen_session_id")),
            evidence=[str(item) for item in payload.get("evidence", [])],
        )

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "term": self.term,
            "meaning": self.meaning,
            "salience": self.salience,
            "evidence": list(self.evidence),
        }
        if self.tone is not None:
            payload["tone"] = self.tone
        if self.first_seen_session_id is not None:
            payload["first_seen_session_id"] = str(self.first_seen_session_id)
        if self.last_seen_session_id is not None:
            payload["last_seen_session_id"] = str(self.last_seen_session_id)
        return payload


@dataclass(frozen=True)
class PersonaTomokoPhrase:
    phrase: str
    usage: str
    salience: float
    evidence_session_id: UUID | None = None

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> PersonaTomokoPhrase:
        return cls(
            phrase=str(payload.get("phrase", "")),
            usage=str(payload.get("usage", "")),
            salience=float(payload.get("salience", 0.0)),
            evidence_session_id=_optional_uuid(payload.get("evidence_session_id")),
        )

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "phrase": self.phrase,
            "usage": self.usage,
            "salience": self.salience,
        }
        if self.evidence_session_id is not None:
            payload["evidence_session_id"] = str(self.evidence_session_id)
        return payload


@dataclass(frozen=True)
class PersonaRelationshipMarker:
    marker: str
    meaning: str
    salience: float

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> PersonaRelationshipMarker:
        return cls(
            marker=str(payload.get("marker", "")),
            meaning=str(payload.get("meaning", "")),
            salience=float(payload.get("salience", 0.0)),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "marker": self.marker,
            "meaning": self.meaning,
            "salience": self.salience,
        }


@dataclass(frozen=True)
class PersonaCorrection:
    wrong: str
    correct: str
    source_session_id: UUID | None = None

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> PersonaCorrection:
        return cls(
            wrong=str(payload.get("wrong", "")),
            correct=str(payload.get("correct", "")),
            source_session_id=_optional_uuid(payload.get("source_session_id")),
        )

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "wrong": self.wrong,
            "correct": self.correct,
        }
        if self.source_session_id is not None:
            payload["source_session_id"] = str(self.source_session_id)
        return payload


@dataclass(frozen=True)
class PersonaLexiconSnapshot:
    user_terms: list[PersonaLexiconTerm] = field(default_factory=list)
    tomoko_phrases: list[PersonaTomokoPhrase] = field(default_factory=list)
    relationship_markers: list[PersonaRelationshipMarker] = field(default_factory=list)
    corrections: list[PersonaCorrection] = field(default_factory=list)
    schema_version: int = 1

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> PersonaLexiconSnapshot:
        schema_version = int(payload.get("schema_version", 1))
        if schema_version > 1:
            raise ValueError(
                f"Unsupported persona lexicon schema_version: {schema_version}"
            )
        return cls(
            schema_version=1,
            user_terms=[
                PersonaLexiconTerm.from_json(item)
                for item in payload.get("user_terms", [])
            ],
            tomoko_phrases=[
                PersonaTomokoPhrase.from_json(item)
                for item in payload.get("tomoko_phrases", [])
            ],
            relationship_markers=[
                PersonaRelationshipMarker.from_json(item)
                for item in payload.get("relationship_markers", [])
            ],
            corrections=[
                PersonaCorrection.from_json(item)
                for item in payload.get("corrections", [])
            ],
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "user_terms": [item.to_json() for item in self.user_terms],
            "tomoko_phrases": [item.to_json() for item in self.tomoko_phrases],
            "relationship_markers": [
                item.to_json() for item in self.relationship_markers
            ],
            "corrections": [item.to_json() for item in self.corrections],
        }

    def select_terms_for_prompt(self, *, query: str, limit: int) -> list[LexiconTerm]:
        query_text = query.strip()
        terms = sorted(self.user_terms, key=lambda item: item.salience, reverse=True)
        if query_text:
            matched = [
                item
                for item in terms
                if item.term in query_text
                or query_text in item.term
                or any(query_text in evidence for evidence in item.evidence)
            ]
            remaining = [item for item in terms if item not in matched]
            terms = matched + remaining
        return [
            LexiconTerm(
                term=item.term,
                meaning=item.meaning,
                salience=item.salience,
                tone=item.tone,
            )
            for item in terms[:limit]
        ]


@dataclass(frozen=True)
class PersonaRelationship:
    familiarity: float = 0.0
    preferred_address: str | None = None
    boundaries: list[str] = field(default_factory=list)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> PersonaRelationship:
        return cls(
            familiarity=float(payload.get("familiarity", 0.0)),
            preferred_address=_optional_str(payload.get("preferred_address")),
            boundaries=[str(item) for item in payload.get("boundaries", [])],
        )

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "familiarity": self.familiarity,
            "boundaries": list(self.boundaries),
        }
        if self.preferred_address is not None:
            payload["preferred_address"] = self.preferred_address
        return payload


@dataclass(frozen=True)
class PersonaSpeakingStyle:
    sentence_length: str | None = None
    honorific_level: str | None = None
    signature_phrases: list[str] = field(default_factory=list)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> PersonaSpeakingStyle:
        return cls(
            sentence_length=_optional_str(payload.get("sentence_length")),
            honorific_level=_optional_str(payload.get("honorific_level")),
            signature_phrases=[
                str(item) for item in payload.get("signature_phrases", [])
            ],
        )

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "signature_phrases": list(self.signature_phrases),
        }
        if self.sentence_length is not None:
            payload["sentence_length"] = self.sentence_length
        if self.honorific_level is not None:
            payload["honorific_level"] = self.honorific_level
        return payload


@dataclass(frozen=True)
class PersonaOpenThread:
    topic: str
    status: str
    source_session_id: UUID | None = None

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> PersonaOpenThread:
        return cls(
            topic=str(payload.get("topic", "")),
            status=str(payload.get("status", "")),
            source_session_id=_optional_uuid(payload.get("source_session_id")),
        )

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "topic": self.topic,
            "status": self.status,
        }
        if self.source_session_id is not None:
            payload["source_session_id"] = str(self.source_session_id)
        return payload


@dataclass(frozen=True)
class PersonaStateSnapshot:
    traits: dict[str, float] = field(default_factory=dict)
    relationship: PersonaRelationship = field(default_factory=PersonaRelationship)
    speaking_style: PersonaSpeakingStyle = field(default_factory=PersonaSpeakingStyle)
    open_threads: list[PersonaOpenThread] = field(default_factory=list)
    schema_version: int = 1

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> PersonaStateSnapshot:
        schema_version = int(payload.get("schema_version", 1))
        if schema_version > 1:
            raise ValueError(
                f"Unsupported persona state schema_version: {schema_version}"
            )
        return cls(
            schema_version=1,
            traits={
                str(key): float(value)
                for key, value in payload.get("traits", {}).items()
            },
            relationship=PersonaRelationship.from_json(
                payload.get("relationship", {})
            ),
            speaking_style=PersonaSpeakingStyle.from_json(
                payload.get("speaking_style", {})
            ),
            open_threads=[
                PersonaOpenThread.from_json(item)
                for item in payload.get("open_threads", [])
            ],
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "traits": dict(self.traits),
            "relationship": self.relationship.to_json(),
            "speaking_style": self.speaking_style.to_json(),
            "open_threads": [item.to_json() for item in self.open_threads],
        }

    def to_prompt_slice(self) -> PersonaPromptSlice:
        return PersonaPromptSlice(
            traits=dict(self.traits),
            relationship_familiarity=self.relationship.familiarity,
            preferred_address=self.relationship.preferred_address,
            sentence_length=self.speaking_style.sentence_length,
            honorific_level=self.speaking_style.honorific_level,
            signature_phrases=list(self.speaking_style.signature_phrases),
        )


@dataclass(frozen=True)
class PersonaDiffEntry:
    path: str
    reason: str
    value: Any | None = None
    from_value: Any | None = None
    to_value: Any | None = None

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> PersonaDiffEntry:
        return cls(
            path=str(payload.get("path", "")),
            reason=str(payload.get("reason", "")),
            value=payload.get("value"),
            from_value=payload.get("from"),
            to_value=payload.get("to"),
        )

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "path": self.path,
            "reason": self.reason,
        }
        if self.value is not None:
            payload["value"] = self.value
        if self.from_value is not None:
            payload["from"] = self.from_value
        if self.to_value is not None:
            payload["to"] = self.to_value
        return payload


@dataclass(frozen=True)
class PersonaVersionDiff:
    added: list[PersonaDiffEntry] = field(default_factory=list)
    updated: list[PersonaDiffEntry] = field(default_factory=list)
    deprecated: list[PersonaDiffEntry] = field(default_factory=list)
    schema_version: int = 1

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> PersonaVersionDiff:
        schema_version = int(payload.get("schema_version", 1))
        if schema_version > 1:
            raise ValueError(
                f"Unsupported persona diff schema_version: {schema_version}"
            )
        return cls(
            schema_version=1,
            added=[
                PersonaDiffEntry.from_json(item) for item in payload.get("added", [])
            ],
            updated=[
                PersonaDiffEntry.from_json(item)
                for item in payload.get("updated", [])
            ],
            deprecated=[
                PersonaDiffEntry.from_json(item)
                for item in payload.get("deprecated", [])
            ],
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "added": [item.to_json() for item in self.added],
            "updated": [item.to_json() for item in self.updated],
            "deprecated": [item.to_json() for item in self.deprecated],
        }


@dataclass(frozen=True)
class LexiconTerm:
    term: str
    meaning: str
    salience: float
    tone: str | None = None


@dataclass(frozen=True)
class PersonaPromptSlice:
    traits: dict[str, float]
    relationship_familiarity: float
    preferred_address: str | None
    sentence_length: str | None
    honorific_level: str | None
    signature_phrases: list[str]


@dataclass(frozen=True)
class ContextBuildPolicy:
    depth: ContextDepth
    max_build_ms: int
    max_prompt_tokens: int
    max_same_session_turns: int
    max_recent_turns: int
    max_session_summaries: int
    max_memory_hits: int
    max_lexicon_terms: int
    allow_turn_memory_search: bool
    allow_persona_slice: bool
    max_parallel_sources: int = 6
    prioritize_session_summaries: bool = True
    max_calendar_events: int = 0
    allow_calendar_context: bool = False
    max_research_results: int = 0
    allow_research_results: bool = False
    max_task_ledger_entries: int = 0
    allow_task_ledger: bool = False

    @classmethod
    def for_depth(cls, depth: ContextDepth) -> ContextBuildPolicy:
        match depth:
            case "fast":
                return cls(
                    depth=depth,
                    max_build_ms=20,
                    max_prompt_tokens=1200,
                    max_same_session_turns=12,
                    max_recent_turns=12,
                    max_session_summaries=0,
                    max_memory_hits=0,
                    max_lexicon_terms=0,
                    allow_turn_memory_search=False,
                    allow_persona_slice=False,
                    max_task_ledger_entries=10,
                    allow_task_ledger=True,
                )
            case "normal":
                return cls(
                    depth=depth,
                    max_build_ms=50,
                    max_prompt_tokens=1800,
                    max_same_session_turns=12,
                    max_recent_turns=12,
                    max_session_summaries=3,
                    max_memory_hits=0,
                    max_lexicon_terms=5,
                    allow_turn_memory_search=False,
                    allow_persona_slice=True,
                    max_task_ledger_entries=10,
                    allow_task_ledger=True,
                )
            case "deep":
                return cls(
                    depth=depth,
                    max_build_ms=100,
                    max_prompt_tokens=2600,
                    max_same_session_turns=12,
                    max_recent_turns=12,
                    max_session_summaries=3,
                    max_memory_hits=5,
                    max_lexicon_terms=8,
                    allow_turn_memory_search=True,
                    allow_persona_slice=True,
                    max_calendar_events=64,
                    allow_calendar_context=True,
                    max_research_results=3,
                    allow_research_results=True,
                    max_task_ledger_entries=25,
                    allow_task_ledger=True,
                )
            case "reflective":
                return cls(
                    depth=depth,
                    max_build_ms=500,
                    max_prompt_tokens=6000,
                    max_same_session_turns=24,
                    max_recent_turns=24,
                    max_session_summaries=8,
                    max_memory_hits=12,
                    max_lexicon_terms=20,
                    allow_turn_memory_search=True,
                    allow_persona_slice=True,
                    max_calendar_events=16,
                    allow_calendar_context=True,
                    max_research_results=8,
                    allow_research_results=True,
                    max_task_ledger_entries=50,
                    allow_task_ledger=True,
                )


@dataclass(frozen=True)
class ContextCacheTrace:
    hit: bool
    age_ms: float | None
    ttl_ms: int


@dataclass(frozen=True)
class ContextSourceScoreTrace:
    source: str
    source_id: str | None
    speaker: str | None
    selected: bool
    dropped_reason: str | None
    raw_similarity: float | None
    base_score: float | None
    source_weight: float
    role_weight: float
    recency_weight: float
    salience_weight: float
    final_score: float
    quota_hit: bool = False


@dataclass(frozen=True)
class ContextBuildTrace:
    budget_ms: int
    elapsed_ms: float
    timed_out: bool
    depth: ContextDepth
    included_counts: dict[str, int]
    skipped_sources: list[str]
    stage_timings_ms: dict[str, float]
    cache_hits: dict[str, bool]
    source_errors: dict[str, str]
    skipped_reasons: dict[str, str] = field(default_factory=dict)
    cache_entries: dict[str, ContextCacheTrace] = field(default_factory=dict)
    cue_type: str = "normal"
    source_score_traces: list[ContextSourceScoreTrace] = field(default_factory=list)


@dataclass(frozen=True)
class TomokoContextSnapshot:
    depth: ContextDepth
    recent_turns: list[ConversationTurn]
    session_summaries: list[SessionSummaryHit]
    memory_hits: list[MemoryHit]
    lexicon_terms: list[LexiconTerm]
    persona_slice: PersonaPromptSlice | None
    token_budget_hint: int
    build_elapsed_ms: float
    source_counts: dict[str, int]
    trace: ContextBuildTrace
    calendar_events: list[CalendarEvent] = field(default_factory=list)
    research_results: list[ResearchContextHit] = field(default_factory=list)
    task_ledger_entries: list[TaskLedgerEntry] = field(default_factory=list)


@dataclass(frozen=True)
class CalendarEvent:
    source_id: str
    uid: str
    summary: str
    start_time: datetime
    end_time: datetime | None
    all_day: bool
    description: str = ""
    location: str = ""
    status: str = "confirmed"

    @property
    def source_key(self) -> str:
        return f"{self.source_id}:{self.uid}:{self.start_time.isoformat()}"


@dataclass
class ThinkingInput:
    text: str
    speaker: str | None
    context: list[ConversationTurn]
    emotion: str
    device_id: str
    long_term_memory: list[MemoryHit] = field(default_factory=list)
    short_memory_notes: list[ShortMemoryNote] = field(default_factory=list)
    context_snapshot: TomokoContextSnapshot | None = None
    response_directive: str | None = None


@dataclass(slots=True)
class ThinkingEvent:
    type: Literal["emotion", "text_delta", "done"]
    value: str


@dataclass
class TTSInput:
    text: str
    style: str
    voice: str | None = None


@dataclass(slots=True)
class AudioChunkOut:
    data: bytes
    sequence: int
    is_last: bool


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_uuid(value: object) -> UUID | None:
    if value in (None, ""):
        return None
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _optional_datetime_value(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _float_or_zero(value: object) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


def _clamp_float(value: object, *, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = minimum
    return min(maximum, max(minimum, number))
