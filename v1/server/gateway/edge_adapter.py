from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from server.gateway.dedup import DuplicateSpeechFilter
from server.gateway.presence import PresenceManager
from server.session import TomoroSession
from server.shared.edge_protocol import (
    EdgeHelloEvent,
    EdgePlaybackTelemetryEvent,
    EdgePresenceEvent,
    EdgeSpeechEvent,
)
from server.shared.models import PlaybackTelemetry, Transcript

logger = logging.getLogger(__name__)


@dataclass
class GatewayEdgeProtocolHandler:
    session: TomoroSession
    presence_manager: PresenceManager
    duplicate_filter: DuplicateSpeechFilter
    stale_after_ms: int = 5000
    seen_event_ids: set[UUID] = field(default_factory=set)
    device_id: str | None = None

    async def handle(
        self,
        event: EdgeHelloEvent | EdgePresenceEvent | EdgeSpeechEvent | EdgePlaybackTelemetryEvent,
    ) -> None:
        if event.event_id in self.seen_event_ids:
            logger.info("ignored duplicate edge event id=%s type=%s", event.event_id, event.type)
            return
        self.seen_event_ids.add(event.event_id)

        if isinstance(event, EdgeHelloEvent):
            self.device_id = event.device_id
            await self.presence_manager.store.upsert_edge_status(
                device_id=event.device_id,
                status="online",
                last_seen_at=event.sent_at,
                role="edge",
            )
            return
        if isinstance(event, EdgePresenceEvent):
            await self.presence_manager.report(
                device_id=event.device_id,
                audio_level_db=event.audio_level_db,
                observed_at=event.observed_at,
                is_speaking=event.is_speaking,
            )
            return
        if isinstance(event, EdgePlaybackTelemetryEvent):
            await self.session.handle_playback_telemetry(
                PlaybackTelemetry(
                    type=event.type,
                    turn_id=event.turn_id,
                    chunk_id=event.chunk_id,
                    scheduled_audio_time=event.scheduled_audio_time,
                    sent_audio_time=event.sent_audio_time,
                    audio_context_time=event.audio_context_time,
                    performance_now_ms=event.performance_now_ms,
                )
            )
            return
        await self._handle_speech(event)

    async def _handle_speech(self, event: EdgeSpeechEvent) -> None:
        if _is_stale(event.observed_at, stale_after_ms=self.stale_after_ms):
            logger.info(
                "ignored stale edge speech device_id=%s transcript_id=%s observed_at=%s",
                event.device_id,
                event.transcript_id,
                event.observed_at,
            )
            return
        await self.presence_manager.report(
            device_id=event.device_id,
            audio_level_db=event.audio_level_db,
            observed_at=event.observed_at,
            transcript_id=event.transcript_id,
            transcript_text=event.transcript,
            is_speaking=True,
        )
        primary = await self.presence_manager.resolve_primary(now=event.observed_at)
        if primary is not None and primary.device_id != event.device_id:
            logger.info(
                "ignored non-primary edge speech device_id=%s primary_device_id=%s",
                event.device_id,
                primary.device_id,
            )
            return
        if await self.duplicate_filter.is_duplicate(
            event.transcript,
            device_id=event.device_id,
            observed_at=event.observed_at,
        ):
            logger.info(
                "ignored duplicate edge speech device_id=%s transcript_id=%s",
                event.device_id,
                event.transcript_id,
            )
            return
        await self.session.process_transcript(
            Transcript(
                text=event.transcript,
                device_id=event.device_id,
                speaker=event.speaker,
                audio_level_db=event.audio_level_db,
                recorded_at=event.observed_at,
                is_final=True,
            )
        )


def _is_stale(observed_at: datetime, *, stale_after_ms: int) -> bool:
    now = datetime.now(UTC)
    observed = observed_at if observed_at.tzinfo is not None else observed_at.replace(tzinfo=UTC)
    return (now - observed).total_seconds() * 1000 > stale_after_ms
