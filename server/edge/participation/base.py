from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from server.shared.models import ParticipationDecision, Transcript


@dataclass(frozen=True)
class ParticipationContext:
    transcript: str
    device_id: str | None = None
    speaker: str | None = None

    @classmethod
    def from_transcript(cls, transcript: Transcript) -> ParticipationContext:
        return cls(
            transcript=transcript.text,
            device_id=transcript.device_id,
            speaker=transcript.speaker,
        )


class ParticipationJudge(ABC):
    @abstractmethod
    async def judge(self, ctx: ParticipationContext) -> ParticipationDecision: ...
