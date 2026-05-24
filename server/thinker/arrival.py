from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from server.shared.candidate import (
    ArrivalBehavior,
    ArrivalCandidate,
    ArrivalContextSnapshot,
    CandidateStore,
)
from server.shared.inference.router import InferenceRouter

logger = logging.getLogger(__name__)

ARRIVAL_OUTPUT_SCHEMA = {
    "behavior": "speak_first | wait_silent | subtle_react",
    "utterance_text": "str | null",
    "reason": "str",
}

_VALID_BEHAVIORS = {"speak_first", "wait_silent", "subtle_react"}
_ARRIVAL_TTL = timedelta(minutes=3)
_URGENT_FETCH_LIMIT = 20
_TOP_URGENT_LIMIT = 3

_SYSTEM_PROMPT = """\
あなたはTomokoの arrival precompute worker です。
人間が今から数分以内に部屋へ来る可能性に備えて、最初のふるまいを決めてください。
返答は JSON object だけにしてください。schema:
{
  "behavior": "speak_first" | "wait_silent" | "subtle_react",
  "utterance_text": "短い自然な日本語" | null,
  "reason": "短い判断理由"
}

behavior の意味:
- speak_first: 入室時に一言話す
- wait_silent: 何も言わず待つ
- subtle_react: Phase 10 以降で表示だけ変える余地を残す
"""


@dataclass(frozen=True)
class ArrivalStats:
    time_since_last_session_sec: int | None = None
    session_count_today: int = 0
    persona_hint: str | None = None


class ArrivalStatsReader(Protocol):
    async def read_arrival_stats(
        self,
        *,
        now: datetime,
        device_id: str | None,
    ) -> ArrivalStats: ...


class ArrivalPrecomputer:
    def __init__(
        self,
        *,
        store: CandidateStore,
        router: InferenceRouter,
        stats_reader: ArrivalStatsReader | None = None,
        ttl: timedelta = _ARRIVAL_TTL,
    ) -> None:
        self.store = store
        self.router = router
        self.stats_reader = stats_reader
        self.ttl = ttl

    async def precompute_once(
        self,
        *,
        now: datetime,
        device_id: str | None,
    ) -> ArrivalCandidate:
        snapshot = await self._build_context_snapshot(now=now, device_id=device_id)
        behavior, utterance_text, reason = await self._decide(snapshot)
        logger.info(
            "arrival precompute behavior=%s device_id=%s reason=%s",
            behavior,
            device_id,
            reason,
        )
        return await self.store.insert_arrival_candidate(
            context_snapshot=snapshot,
            behavior=behavior,
            computed_at=now,
            valid_until=now + self.ttl,
            utterance_text=utterance_text,
        )

    async def _build_context_snapshot(
        self,
        *,
        now: datetime,
        device_id: str | None,
    ) -> ArrivalContextSnapshot:
        active = await self.store.fetch_active_utterance_candidates(
            now=now,
            limit=_URGENT_FETCH_LIMIT,
        )
        urgent = [candidate for candidate in active if candidate.urgent]
        stats = await self._read_stats(now=now, device_id=device_id)
        return ArrivalContextSnapshot(
            computed_at=now,
            device_id=device_id,
            local_time=now.strftime("%H:%M"),
            time_since_last_session_sec=stats.time_since_last_session_sec,
            session_count_today=stats.session_count_today,
            urgent_candidate_count=len(urgent),
            top_urgent_seeds=tuple(
                candidate.seed for candidate in urgent[:_TOP_URGENT_LIMIT]
            ),
            persona_hint=stats.persona_hint,
        )

    async def _read_stats(
        self,
        *,
        now: datetime,
        device_id: str | None,
    ) -> ArrivalStats:
        if self.stats_reader is None:
            return ArrivalStats()
        try:
            return await self.stats_reader.read_arrival_stats(
                now=now,
                device_id=device_id,
            )
        except Exception as exc:
            logger.info(
                "arrival stats unavailable device_id=%s reason=%s",
                device_id,
                type(exc).__name__,
            )
            return ArrivalStats()

    async def _decide(
        self,
        snapshot: ArrivalContextSnapshot,
    ) -> tuple[ArrivalBehavior, str | None, str]:
        try:
            backend = await self.router.select("candidate_gen", "privacy")
            raw_text = "".join(
                [
                    chunk
                    async for chunk in backend.chat_stream(
                        _SYSTEM_PROMPT,
                        [_user_message(snapshot)],
                    )
                ]
            )
            return _parse_arrival_decision(raw_text)
        except Exception as exc:
            logger.info(
                "arrival precompute fallback wait_silent reason=%s",
                type(exc).__name__,
            )
            return "wait_silent", None, "fallback"


def _user_message(snapshot: ArrivalContextSnapshot) -> dict[str, str]:
    sections = [
        f"computed_at: {snapshot.computed_at.isoformat()}",
        f"device_id: {snapshot.device_id or 'unknown'}",
        f"local_time: {snapshot.local_time}",
        "time_since_last_session_sec: "
        f"{snapshot.time_since_last_session_sec}",
        f"session_count_today: {snapshot.session_count_today}",
        f"urgent_candidate_count: {snapshot.urgent_candidate_count}",
        _format_urgent_seeds(snapshot.top_urgent_seeds),
        f"persona_hint: {snapshot.persona_hint or ''}",
    ]
    return {"role": "user", "content": "\n".join(sections)}


def _format_urgent_seeds(candidates: tuple[str, ...]) -> str:
    if not candidates:
        return "top_urgent_seeds: []"
    return "top_urgent_seeds:\n" + "\n".join(f"- {seed}" for seed in candidates)


def _parse_arrival_decision(raw_text: str) -> tuple[ArrivalBehavior, str | None, str]:
    payload = _load_json_object(raw_text)
    behavior = str(payload.get("behavior") or "wait_silent")
    utterance_text = _optional_text(payload.get("utterance_text"))
    reason = str(payload.get("reason") or "no reason")

    if behavior not in _VALID_BEHAVIORS:
        return "wait_silent", None, f"invalid behavior: {behavior}"
    if behavior == "speak_first" and utterance_text is None:
        return "wait_silent", None, "speak_first missing utterance_text"
    if behavior != "speak_first":
        utterance_text = None
    return behavior, utterance_text, reason  # type: ignore[return-value]


def _load_json_object(raw_text: str) -> dict[str, Any]:
    stripped = raw_text.strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(stripped[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("arrival response must be a JSON object")
    return payload


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
