from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from server.shared.candidate import CandidateSeed, ThinkerSourceContext
from server.shared.models import UserContextSnapshot
from server.shared.perception import UserContextSnapshotStore


@dataclass(frozen=True)
class _CandidateShape:
    priority: float
    ttl: timedelta
    intrusion: str


_SCREEN_SOURCE = "screen_context"
_ACTIVITY_SOURCE = "activity_context"


class ScreenContextSource:
    def __init__(self, *, snapshot_store: UserContextSnapshotStore) -> None:
        self.snapshot_store = snapshot_store

    async def collect(self, context: ThinkerSourceContext) -> list[CandidateSeed]:
        snapshot = await _latest_snapshot(self.snapshot_store)
        if snapshot is None or not snapshot.screen_activity_label:
            return []
        if snapshot.interaction_readiness in {"away", "do_not_disturb"}:
            return []
        shape = _shape_for(snapshot)
        label = snapshot.screen_activity_label
        return [
            CandidateSeed(
                seed_text=(
                    f"画面では {label} が続いている。"
                    "邪魔にならない範囲で、必要なら手伝えると短く声をかける。"
                ),
                source=_SCREEN_SOURCE,
                priority=shape.priority,
                urgent=False,
                expires_at=context.observed_at + shape.ttl,
                dedupe_key=_dedupe_key(_SCREEN_SOURCE, snapshot, label),
                context_tags=(
                    "screen_context",
                    f"readiness:{snapshot.interaction_readiness}",
                    f"intrusion:{shape.intrusion}",
                ),
                metadata_json=_metadata(snapshot, label=label, intrusion=shape.intrusion),
            )
        ]


class ActivityContextSource:
    def __init__(self, *, snapshot_store: UserContextSnapshotStore) -> None:
        self.snapshot_store = snapshot_store

    async def collect(self, context: ThinkerSourceContext) -> list[CandidateSeed]:
        snapshot = await _latest_snapshot(self.snapshot_store)
        if snapshot is None or not snapshot.activity_label:
            return []
        if snapshot.interaction_readiness in {"away", "do_not_disturb"}:
            return []
        shape = _shape_for(snapshot)
        label = snapshot.activity_label
        return [
            CandidateSeed(
                seed_text=(
                    f"今は {label} で、軽く話しかけてもよさそう。"
                    "押しつけずに短く様子を聞く。"
                ),
                source=_ACTIVITY_SOURCE,
                priority=min(shape.priority, 0.55),
                urgent=False,
                expires_at=context.observed_at + shape.ttl,
                dedupe_key=_dedupe_key(_ACTIVITY_SOURCE, snapshot, label),
                context_tags=(
                    "activity_context",
                    f"readiness:{snapshot.interaction_readiness}",
                    f"intrusion:{shape.intrusion}",
                ),
                metadata_json=_metadata(snapshot, label=label, intrusion=shape.intrusion),
            )
        ]


async def _latest_snapshot(
    snapshot_store: UserContextSnapshotStore,
) -> UserContextSnapshot | None:
    snapshots = await snapshot_store.fetch_latest(limit=1)
    return snapshots[0] if snapshots else None


def _shape_for(snapshot: UserContextSnapshot) -> _CandidateShape:
    if snapshot.interaction_readiness == "needs_help_maybe":
        return _CandidateShape(
            priority=0.72,
            ttl=timedelta(minutes=20),
            intrusion="low",
        )
    if snapshot.interaction_readiness == "chat_ok":
        return _CandidateShape(
            priority=0.55,
            ttl=timedelta(minutes=30),
            intrusion="normal",
        )
    return _CandidateShape(
        priority=0.35,
        ttl=timedelta(minutes=20),
        intrusion="low",
    )


def _dedupe_key(
    source: str,
    snapshot: UserContextSnapshot,
    label: str,
) -> str:
    return f"{source}:{snapshot.computed_at.isoformat()}:{_slug(label)}"


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "unknown"


def _metadata(
    snapshot: UserContextSnapshot,
    *,
    label: str,
    intrusion: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "snapshot_id": str(snapshot.id) if snapshot.id is not None else None,
        "snapshot_computed_at": snapshot.computed_at.isoformat(),
        "interaction_readiness": snapshot.interaction_readiness,
        "label": label,
        "intrusion": intrusion,
        "confidence": snapshot.confidence,
    }
