from __future__ import annotations

from dataclasses import dataclass

from server.shared.models import DurableUtterance, PartialTranscriptObservation
from server.tomoko.session import SessionBoundaryModel


@dataclass(slots=True)
class TomokoProcessCore:
    session_model: SessionBoundaryModel

    def adopt_final_observation(
        self,
        observation: PartialTranscriptObservation,
    ) -> DurableUtterance | None:
        if not observation.is_final:
            return None
        if not observation.text.strip():
            return None
        boundary = self.session_model.observe_utterance(observation.audio_ended_at)
        return DurableUtterance(
            session_id=boundary.session_id,
            speaker="user",
            text=observation.text,
            stt_observation_id=observation.id,
            trace_id=observation.trace_id,
        )
