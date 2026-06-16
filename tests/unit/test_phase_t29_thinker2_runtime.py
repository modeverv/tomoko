from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from server.shared.candidate import InMemoryCandidateStore
from server.shared.models import UserContextSnapshot
from server.shared.perception import InMemoryUserContextSnapshotStore
from server.thinker.sources.context_snapshot import ScreenContextSource
from server.thinker2.main import (
    Thinker2Process,
    Thinker2RunResult,
    render_thinker2_inspection_html,
)


@pytest.mark.unit
async def test_thinker2_run_once_builds_snapshot_and_inserts_candidates() -> None:
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    snapshot_store = InMemoryUserContextSnapshotStore()
    candidate_store = InMemoryCandidateStore()
    process = Thinker2Process(
        snapshot_builder=StaticSnapshotBuilder(
            snapshot_store=snapshot_store,
            snapshot=_snapshot(now),
        ),
        candidate_store=candidate_store,
        candidate_sources=[ScreenContextSource(snapshot_store=snapshot_store)],
    )

    result = await process.run_once(now=now)
    active = await candidate_store.fetch_active_utterance_candidates(now=now, limit=10)

    assert result.snapshot_readiness == "needs_help_maybe"
    assert result.candidate_generated_count == 1
    assert result.candidate_inserted_count == 1
    assert result.queue_depths["candidate_sources"] == 1
    assert result.inference_latency_ms["snapshot_build"] >= 0
    assert result.skipped_stale_frame_count == 0
    assert active[0].source == "screen_context"


@pytest.mark.unit
def test_render_thinker2_inspection_html_contains_runtime_counts() -> None:
    html = render_thinker2_inspection_html(
        Thinker2RunResult(
            snapshot_readiness="needs_help_maybe",
            snapshot_summary="user=debugging",
            candidate_generated_count=2,
            candidate_inserted_count=1,
            queue_depths={"candidate_sources": 2},
            inference_latency_ms={"snapshot_build": 12.5},
            skipped_stale_frame_count=3,
            skipped_backlog_frame_count=4,
            elapsed_ms=20.0,
        )
    )

    assert "thinker2 inspection" in html
    assert "needs_help_maybe" in html
    assert "candidate_inserted_count" in html
    assert "skipped_stale_frame_count" in html


class StaticSnapshotBuilder:
    def __init__(
        self,
        *,
        snapshot_store: InMemoryUserContextSnapshotStore,
        snapshot: UserContextSnapshot,
    ) -> None:
        self.snapshot_store = snapshot_store
        self.snapshot = snapshot

    async def build_once(self, *, now: datetime):
        del now
        snapshot = await self.snapshot_store.insert_snapshot(self.snapshot)
        return StaticSnapshotBuildResult(snapshot=snapshot)


class StaticSnapshotBuildResult:
    def __init__(self, *, snapshot: UserContextSnapshot) -> None:
        self.snapshot = snapshot
        self.trace = StaticTrace()


class StaticTrace:
    elapsed_ms = 1.0
    source_counts = {"screen": 1}
    skipped_sources = ()
    source_errors = {}


def _snapshot(now: datetime) -> UserContextSnapshot:
    return UserContextSnapshot(
        computed_at=now,
        present=True,
        screen_activity_label="debugging tests",
        user_activity_summary="present; screen=debugging tests",
        context_summary="user=present; readiness=needs_help_maybe",
        interaction_readiness="needs_help_maybe",
        confidence=0.8,
        created_at=now - timedelta(seconds=1),
    )
