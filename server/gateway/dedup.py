from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Protocol

from server.gateway.turn_taking.barge_in import BargeInDetector


@dataclass(frozen=True)
class RecentTranscript:
    text: str
    device_id: str
    recorded_at: datetime


class RecentTranscriptReader(Protocol):
    async def read_recent_transcripts(
        self,
        *,
        since: datetime,
        exclude_device_id: str,
        limit: int,
    ) -> tuple[RecentTranscript, ...]: ...


class DuplicateSpeechFilter:
    def __init__(
        self,
        *,
        reader: RecentTranscriptReader,
        window: timedelta = timedelta(seconds=2),
        similarity_threshold: float = 0.88,
        limit: int = 20,
    ) -> None:
        self.reader = reader
        self.window = window
        self.similarity_threshold = similarity_threshold
        self.limit = limit

    async def is_duplicate(
        self,
        transcript: str,
        *,
        device_id: str,
        observed_at: datetime,
    ) -> bool:
        normalized = _normalize(transcript)
        if not normalized:
            return False
        if _contains_any(normalized, BargeInDetector.HARD_INTERRUPTS):
            return False

        recent = await self.reader.read_recent_transcripts(
            since=observed_at - self.window,
            exclude_device_id=device_id,
            limit=self.limit,
        )
        for candidate in recent:
            if candidate.device_id == device_id:
                continue
            candidate_text = _normalize(candidate.text)
            if not candidate_text:
                continue
            if normalized in candidate_text or candidate_text in normalized:
                return True
            if SequenceMatcher(None, normalized, candidate_text).ratio() >= (
                self.similarity_threshold
            ):
                return True
        return False


def _normalize(text: str) -> str:
    text = text.casefold()
    return re.sub(r"[\s、。！？!?「」『』（）()・,.]+", "", text)


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    return any(_normalize(word) in text for word in words)
