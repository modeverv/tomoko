from __future__ import annotations

from collections.abc import Sequence

from server.shared.presence import PresenceReport


class DirectSpeakerResolver:
    """Selects the authoritative edge for one utterance without doing I/O."""

    def resolve(self, reports: Sequence[PresenceReport]) -> PresenceReport | None:
        candidates = [report for report in reports if report.is_speaking]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda report: (
                report.audio_level_db,
                report.observed_at,
                report.device_id,
            ),
        )
