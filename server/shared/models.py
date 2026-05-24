from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import numpy as np

AttentionMode = Literal["ambient", "engaged", "cooldown", "withdrawn"]
ParticipationMode = Literal["called", "invited", "observer", "withdraw"]
BargeInKind = Literal[
    "echo",
    "backchannel",
    "soft_interrupt",
    "hard_interrupt",
    "new_question",
]
BargeInAction = Literal["continue_speaking", "finish_sentence", "restart_turn"]
PlaybackEventType = Literal["playback_started", "playback_ended"]
TranscriptFilterAction = Literal["accept", "suppress_partial", "drop"]
ConversationLogStatus = Literal["completed", "interrupted", "cancelled", "error"]
SummaryStatus = Literal["not_ready", "pending", "processing", "completed", "error"]
PersonaVersionStatus = Literal["completed", "error"]


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
class PlaybackTelemetry:
    type: PlaybackEventType
    turn_id: str | None
    chunk_id: int | None = None
    scheduled_audio_time: float | None = None
    sent_audio_time: float | None = None
    audio_context_time: float | None = None
    performance_now_ms: float | None = None


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


@dataclass(frozen=True)
class SessionSummaryHit:
    session_id: UUID
    summary_text: str
    started_at: datetime
    ended_at: datetime | None
    similarity: float


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


@dataclass
class ThinkingInput:
    text: str
    speaker: str | None
    context: list[ConversationTurn]
    emotion: str
    device_id: str
    long_term_memory: list[MemoryHit] = field(default_factory=list)


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
