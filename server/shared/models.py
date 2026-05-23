from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import numpy as np


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
    mode: Literal["called", "invited", "observer", "withdraw"]
    reason: str


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
