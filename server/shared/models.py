from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

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
    audio_context_time: float | None = None
    performance_now_ms: float | None = None


@dataclass(frozen=True)
class ParticipationContext:
    transcript: str
    attention_mode: AttentionMode = "ambient"
    device_id: str | None = None
    speaker: str | None = None

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
        )


@dataclass
class ConversationTurn:
    speaker: Literal["user", "tomoko"]
    text: str
    timestamp: datetime
    emotion: str | None = None


@dataclass
class ThinkingInput:
    text: str
    speaker: str | None
    context: list[ConversationTurn]
    emotion: str
    device_id: str


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
