from __future__ import annotations

import logging
from datetime import UTC, datetime

from server.session import TomoroSession
from server.shared.candidate import CandidateStore
from server.shared.models import SessionCommand, SessionEvent, TransitionResult
from server.thinker.selection.highest import HighestPriority

logger = logging.getLogger(__name__)


def datetime_now_utc() -> datetime:
    return datetime.now(UTC)


class CandidateCommandRunner:
    def __init__(
        self,
        *,
        session: TomoroSession,
        store: CandidateStore,
        device_id: str | None,
        now_factory=datetime_now_utc,
    ) -> None:
        self.session = session
        self.store = store
        self.device_id = device_id
        self.now_factory = now_factory
        self.selector = HighestPriority()

    async def run_result(self, result: TransitionResult) -> None:
        for command in result.commands:
            next_result = await self.run_command(command)
            if next_result is not None:
                await self.run_result(next_result)

    async def run_command(self, command: SessionCommand) -> TransitionResult | None:
        try:
            return await self._run_command(command)
        except Exception as exc:
            logger.warning(
                "candidate command failed type=%s reason=%s",
                command.type,
                exc,
            )
            return await self.session.post_event(
                SessionEvent(
                    type="candidate_command_failed",
                    payload={"command_type": command.type, "error": str(exc)},
                )
            )

    async def _run_command(self, command: SessionCommand) -> TransitionResult | None:
        now = self.now_factory()
        if command.type == "fetch_initiative_candidate":
            dismissed_count = await self.store.mark_expired_utterance_candidates(now)
            candidates = await self.store.fetch_active_utterance_candidates(
                now=now,
                limit=20,
            )
            selected = self._select_initiative_candidate(candidates)
            logger.info(
                "initiative candidate fetched selected=%s active_count=%s "
                "dismissed_expired_count=%s",
                getattr(selected, "id", None),
                len(candidates),
                dismissed_count,
            )
            return await self.session.post_event(
                SessionEvent(
                    type="initiative_candidate_loaded",
                    payload={
                        "candidate": selected,
                        "request_id": command.payload.get("request_id"),
                    },
                    occurred_at=now,
                )
            )

        if command.type == "fetch_arrival_candidate":
            device_id = command.payload.get("device_id")
            if device_id is None:
                device_id = self.device_id
            candidate = await self.store.fetch_latest_fresh_arrival_candidate(
                now=now,
                device_id=str(device_id) if device_id is not None else None,
            )
            logger.info(
                "arrival candidate fetched selected=%s behavior=%s",
                getattr(candidate, "id", None),
                getattr(candidate, "behavior", None),
            )
            return await self.session.post_event(
                SessionEvent(
                    type="arrival_candidate_loaded",
                    payload={
                        "candidate": candidate,
                        "request_id": command.payload.get("request_id"),
                    },
                    occurred_at=now,
                )
            )

        if command.type == "mark_utterance_spoken":
            await self.store.mark_utterance_spoken(
                command.payload["candidate_id"],
                spoken_at=command.payload.get("spoken_at") or now,
            )
            return None

        if command.type == "dismiss_utterance_candidate":
            await self.store.dismiss_utterance_candidate(
                command.payload["candidate_id"],
                dismissed_at=command.payload.get("dismissed_at") or now,
            )
            return None

        if command.type == "mark_arrival_used":
            await self.store.mark_arrival_used(
                command.payload["arrival_candidate_id"],
                used_at=command.payload.get("used_at") or now,
            )
            return None

        if command.type in {"start_initiative_reply", "start_arrival_reply"}:
            await self.session.start_precomputed_reply(
                text=str(command.payload["text"]),
                device_id=self._command_device_id(command),
                reason=str(command.payload.get("reason") or "initiative"),
                audio_data=command.payload.get("generated_audio"),
            )
            return None

        return None

    def _command_device_id(self, command: SessionCommand) -> str:
        device_id = command.payload.get("device_id") or self.device_id
        return str(device_id or "default")

    def _select_initiative_candidate(self, candidates):
        pregenerated = [
            candidate
            for candidate in candidates
            if candidate.maturity >= 2 and candidate.generated_audio is not None
        ]
        if pregenerated:
            return self.selector.select(pregenerated)
        return self.selector.select(candidates)
