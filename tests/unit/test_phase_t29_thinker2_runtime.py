from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from server.shared.candidate import InMemoryCandidateStore
from server.shared.models import PerceptionFrame, UserContextSnapshot
from server.shared.perception import InMemoryUserContextSnapshotStore
from server.thinker.sources.context_snapshot import ScreenContextSource
from server.thinker2.main import (
    CapturedPerceptionFrameSummary,
    PerceptionInferenceSummary,
    Thinker2Process,
    Thinker2RunResult,
    format_thinker2_console_report,
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
    assert result.camera_context_summary == (
        "present=True; activity=debugging; "
        "presence_observed_at=2026-06-16T09:59:50+00:00; "
        "activity_observed_at=2026-06-16T09:59:55+00:00"
    )
    assert result.screen_context_summary == (
        "activity=debugging tests; observed_at=2026-06-16T09:59:58+00:00"
    )
    assert active[0].source == "screen_context"


@pytest.mark.unit
async def test_thinker2_run_once_captures_perception_frames_before_snapshot() -> None:
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    process = Thinker2Process(
        snapshot_builder=StaticSnapshotBuilder(
            snapshot_store=InMemoryUserContextSnapshotStore(),
            snapshot=_snapshot(now),
        ),
        candidate_store=InMemoryCandidateStore(),
        candidate_sources=[],
        capture_workers=[
            FailingCaptureWorker("camera permission denied"),
            StaticCaptureWorker(
                PerceptionFrame(
                    source="camera",
                    file_path="logs/perception/camera/frame.jpg",
                    sha256="a" * 64,
                    captured_at=now,
                    width=640,
                    height=480,
                )
            ),
            StaticCaptureWorker(
                PerceptionFrame(
                    source="screenshot",
                    file_path="logs/perception/screenshot/frame.png",
                    sha256="b" * 64,
                    captured_at=now,
                    width=1920,
                    height=1080,
                )
            ),
        ],
        perception_inference_workers=[
            StaticInferenceWorker(
                PerceptionInferenceSummary(
                    kind="presence",
                    label="present=False",
                    confidence=0.91,
                    model="unit-vlm",
                )
            ),
            StaticInferenceWorker(
                PerceptionInferenceSummary(
                    kind="camera_activity",
                    label="typing",
                    confidence=0.88,
                    model="unit-vlm",
                ),
                skip_when_presence_absent=True,
            ),
            StaticInferenceWorker(
                PerceptionInferenceSummary(
                    kind="screen_activity",
                    label="debugging tests",
                    confidence=0.82,
                    model="unit-vlm",
                    detail="app=Terminal",
                )
            ),
            FailingInferenceWorker("vlm timeout"),
        ],
    )

    result = await process.run_once(now=now)

    assert result.capture_errors == ("FailingCaptureWorker: camera permission denied",)
    assert result.perception_inference_errors == ("FailingInferenceWorker: vlm timeout",)
    assert result.captured_frames == (
        CapturedPerceptionFrameSummary(
            source="camera",
            file_path="logs/perception/camera/frame.jpg",
            width=640,
            height=480,
            sha256_prefix="aaaaaaaaaaaa",
        ),
        CapturedPerceptionFrameSummary(
            source="screenshot",
            file_path="logs/perception/screenshot/frame.png",
            width=1920,
            height=1080,
            sha256_prefix="bbbbbbbbbbbb",
        ),
    )
    assert result.perception_inferences == (
        PerceptionInferenceSummary(
            kind="presence",
            label="present=False",
            confidence=0.91,
            model="unit-vlm",
        ),
        PerceptionInferenceSummary(
            kind="camera_activity",
            label="away",
            confidence=1.0,
            model="presence_gate",
            detail="reason=no human visible",
        ),
        PerceptionInferenceSummary(
            kind="screen_activity",
            label="debugging tests",
            confidence=0.82,
            model="unit-vlm",
            detail="app=Terminal",
        ),
    )


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
            camera_context_summary="present=True; activity=writing",
            screen_context_summary="activity=debugging tests",
            captured_frames=(
                CapturedPerceptionFrameSummary(
                    source="camera",
                    file_path="logs/perception/camera/frame.jpg",
                    width=640,
                    height=480,
                    sha256_prefix="abc123",
                ),
            ),
            capture_errors=("OpenCVCameraCaptureProvider: camera index 0 could not be opened",),
            perception_inferences=(
                PerceptionInferenceSummary(
                    kind="presence",
                    label="present=True",
                    confidence=0.91,
                    model="unit-vlm",
                ),
            ),
            perception_inference_errors=("MlxVlmScreenActivityBackend: invalid JSON",),
            elapsed_ms=20.0,
        )
    )

    assert "thinker2 inspection" in html
    assert "needs_help_maybe" in html
    assert "candidate_inserted_count" in html
    assert "skipped_stale_frame_count" in html
    assert "camera_context_summary" in html
    assert "present=True; activity=writing" in html
    assert "screen_context_summary" in html
    assert "activity=debugging tests" in html
    assert "captured_frames" in html
    assert "logs/perception/camera/frame.jpg" in html
    assert "capture_errors" in html
    assert "camera index 0 could not be opened" in html
    assert "perception_inferences" in html
    assert "present=True" in html
    assert "perception_inference_errors" in html
    assert "invalid JSON" in html


@pytest.mark.unit
def test_format_thinker2_console_report_contains_camera_and_screen_context() -> None:
    report = format_thinker2_console_report(
        Thinker2RunResult(
            snapshot_readiness="chat_ok",
            snapshot_summary="user=present",
            candidate_generated_count=0,
            candidate_inserted_count=0,
            camera_context_summary="present=True; activity=reading",
            screen_context_summary="activity=browser research",
            captured_frames=(
                CapturedPerceptionFrameSummary(
                    source="camera",
                    file_path="logs/perception/camera/frame.jpg",
                    width=640,
                    height=480,
                    sha256_prefix="abc123",
                ),
            ),
            capture_errors=("OpenCVCameraCaptureProvider: camera failed",),
            perception_inferences=(
                PerceptionInferenceSummary(
                    kind="presence",
                    label="present=True",
                    confidence=0.91,
                    model="unit-vlm",
                ),
                PerceptionInferenceSummary(
                    kind="screen_activity",
                    label="debugging tests",
                    confidence=0.82,
                    model="unit-vlm",
                    detail=(
                        "app=Terminal summary=The user is reviewing pytest output "
                        "task=debugging a failing unit test"
                    ),
                ),
            ),
            perception_inference_errors=("MlxVlmActivityBackend: failed",),
        )
    )

    assert "thinker2_context camera=\"present=True; activity=reading\"" in report
    assert "screen=\"activity=browser research\"" in report
    assert (
        "captured=\"camera:logs/perception/camera/frame.jpg 640x480 sha=abc123\""
        in report
    )
    assert "capture_errors=\"OpenCVCameraCaptureProvider: camera failed\"" in report
    assert (
        "perception=\"presence:present=True conf=0.91 model=unit-vlm; "
        "screen_activity:debugging tests conf=0.82 model=unit-vlm app=Terminal "
        "summary=The user is reviewing pytest output task=debugging a failing unit test\""
        in report
    )
    assert "perception_errors=\"MlxVlmActivityBackend: failed\"" in report


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


class StaticCaptureWorker:
    def __init__(self, frame: PerceptionFrame) -> None:
        self.frame = frame

    async def capture_once(self, *, now: datetime | None = None) -> PerceptionFrame:
        del now
        return self.frame


class FailingCaptureWorker:
    def __init__(self, message: str) -> None:
        self.message = message

    async def capture_once(self, *, now: datetime | None = None) -> PerceptionFrame:
        del now
        raise RuntimeError(self.message)


class StaticInferenceWorker:
    def __init__(
        self,
        summary: PerceptionInferenceSummary,
        *,
        skip_when_presence_absent: bool = False,
    ) -> None:
        self.summary = summary
        self.skip_when_presence_absent = skip_when_presence_absent

    async def process_once(
        self,
        *,
        now: datetime | None = None,
    ) -> PerceptionInferenceSummary | None:
        del now
        return self.summary


class FailingInferenceWorker:
    def __init__(self, message: str) -> None:
        self.message = message

    async def process_once(
        self,
        *,
        now: datetime | None = None,
    ) -> PerceptionInferenceSummary | None:
        del now
        raise RuntimeError(self.message)


def _snapshot(now: datetime) -> UserContextSnapshot:
    return UserContextSnapshot(
        computed_at=now,
        present=True,
        presence_observed_at=now - timedelta(seconds=10),
        activity_label="debugging",
        activity_observed_at=now - timedelta(seconds=5),
        screen_activity_label="debugging tests",
        screen_observed_at=now - timedelta(seconds=2),
        user_activity_summary="present; screen=debugging tests",
        context_summary="user=present; readiness=needs_help_maybe",
        interaction_readiness="needs_help_maybe",
        confidence=0.8,
        created_at=now - timedelta(seconds=1),
    )
