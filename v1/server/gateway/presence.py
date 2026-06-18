from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from server.gateway.resolver import DirectSpeakerResolver
from server.shared.presence import PresenceReport, PresenceStore


@dataclass(frozen=True)
class PresenceManager:
    store: PresenceStore
    resolver: DirectSpeakerResolver
    resolve_window: timedelta = timedelta(milliseconds=700)

    async def report(
        self,
        *,
        device_id: str,
        audio_level_db: float,
        observed_at: datetime,
        transcript_id: UUID | None = None,
        transcript_text: str | None = None,
        is_speaking: bool = True,
    ) -> PresenceReport:
        return await self.store.insert_presence_report(
            device_id=device_id,
            audio_level_db=audio_level_db,
            observed_at=observed_at,
            transcript_id=transcript_id,
            transcript_text=transcript_text,
            is_speaking=is_speaking,
        )

    async def resolve_primary(
        self,
        *,
        now: datetime,
        limit: int = 20,
    ) -> PresenceReport | None:
        reports = await self.store.fetch_recent_presence_reports(
            since=now - self.resolve_window,
            limit=limit,
        )
        return self.resolver.resolve(reports)
