from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from server.shared.models import DurableUtterance, PartialTranscriptObservation
from server.tomoko.session import SessionBoundaryModel

DEFAULT_BLOCKED_FINAL_STT_TEXTS: frozenset[str] = frozenset({"", "はい", "い"})


@dataclass(slots=True)
class TomokoProcessCore:
    session_model: SessionBoundaryModel
    blocked_final_stt_texts: frozenset[str] = DEFAULT_BLOCKED_FINAL_STT_TEXTS

    def adopt_final_observation(
        self,
        observation: PartialTranscriptObservation,
        *,
        session_id_override: UUID | None = None,
    ) -> DurableUtterance | None:
        if self.block_reason_for_final_observation(observation) is not None:
            return None

        session_id = session_id_override
        if session_id is None:
            boundary = self.session_model.observe_utterance(observation.audio_ended_at)
            session_id = boundary.session_id
        return DurableUtterance(
            session_id=session_id,
            speaker="user",
            text=observation.text,
            stt_observation_id=observation.id,
            trace_id=observation.trace_id,
        )

    def block_reason_for_final_observation(
        self,
        observation: PartialTranscriptObservation,
    ) -> str | None:
        if not observation.is_final:
            return "not_final"
        normalized = normalize_stt_block_text(observation.text)
        if not normalized:
            return "blank"
        if normalized in self.blocked_final_stt_texts:
            return "dictionary"
        return None


def normalize_stt_block_text(text: str) -> str:
    return "".join(text.split())
