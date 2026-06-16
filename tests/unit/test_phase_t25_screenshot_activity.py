from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from server.shared.perception import (
    InMemoryPerceptionFrameStore,
    InMemoryScreenActivityObservationStore,
    ScreenActivityObservation,
)
from server.thinker.perception.screen import (
    CapturedScreenshotArtifact,
    MlxVlmScreenActivityBackend,
    ScreenActivityInferenceResult,
    ScreenActivityInferenceWorker,
    ScreenshotCaptureWorker,
    parse_screen_activity_inference_json,
)


class StaticScreenshotProvider:
    def __init__(self, artifact: CapturedScreenshotArtifact) -> None:
        self.artifact = artifact

    async def capture(self, *, captured_at: datetime) -> CapturedScreenshotArtifact:
        assert captured_at == self.artifact.captured_at
        return self.artifact


class RecordingScreenBackend:
    model = "unit-screen"

    def __init__(self, result: ScreenActivityInferenceResult) -> None:
        self.result = result
        self.frame_ids: list[UUID] = []

    async def infer_screen_activity(
        self,
        frame_path: str,
        *,
        frame_id: UUID,
    ) -> ScreenActivityInferenceResult:
        del frame_path
        self.frame_ids.append(frame_id)
        return self.result


@pytest.mark.unit
def test_parse_screen_activity_inference_json_keeps_optional_hints() -> None:
    parsed = parse_screen_activity_inference_json(
        {
            "screen_activity_label": "debugging tests",
            "confidence": 0.81,
            "app_hint": "Terminal",
            "document_hint": "pytest",
            "url_hint": None,
            "reason": "pytest output visible",
        },
        model="gemma-e12b-screen",
    )

    assert parsed == ScreenActivityInferenceResult(
        screen_activity_label="debugging tests",
        confidence=0.81,
        model="gemma-e12b-screen",
        app_hint="Terminal",
        document_hint="pytest",
        url_hint=None,
        raw_reason_json={"reason": "pytest output visible"},
    )


@pytest.mark.unit
async def test_mlx_vlm_screen_backend_parses_json_from_image_generation() -> None:
    def fake_loader(model_name: str):
        assert model_name == "gemma-e12b-screen"
        return object(), object()

    def fake_stream_generator(model, processor, prompt, image, max_tokens):
        del model, processor, max_tokens
        assert "screen_activity_label" in prompt
        assert image == "logs/perception/screenshot/frame.png"
        yield '{"screen_activity_label": "reading docs", "confidence": 0.9}'

    backend = MlxVlmScreenActivityBackend(
        model="gemma-e12b-screen",
        model_loader=fake_loader,
        stream_generator=fake_stream_generator,
    )

    result = await backend.infer_screen_activity(
        "logs/perception/screenshot/frame.png",
        frame_id=UUID("00000000-0000-0000-0000-000000000001"),
    )

    assert result.screen_activity_label == "reading docs"
    assert result.confidence == 0.9


@pytest.mark.unit
async def test_screenshot_capture_worker_saves_screenshot_frame() -> None:
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    frame_store = InMemoryPerceptionFrameStore()
    worker = ScreenshotCaptureWorker(
        frame_store=frame_store,
        provider=StaticScreenshotProvider(
            CapturedScreenshotArtifact(
                file_path="logs/perception/screenshot/frame.png",
                sha256="screen-sha",
                captured_at=now,
                width=1920,
                height=1080,
            )
        ),
        device_id="desk",
        retention_limit=1,
    )

    frame = await worker.capture_once(now=now)

    assert frame.source == "screenshot"
    assert frame.device_id == "desk"
    assert frame.width == 1920


@pytest.mark.unit
async def test_screen_activity_worker_processes_latest_screenshot() -> None:
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    frame_store = InMemoryPerceptionFrameStore()
    old_frame = await frame_store.insert_frame(
        source="screenshot",
        file_path="logs/perception/screenshot/old.png",
        sha256="old",
        captured_at=now - timedelta(seconds=20),
    )
    latest_frame = await frame_store.insert_frame(
        source="screenshot",
        file_path="logs/perception/screenshot/latest.png",
        sha256="latest",
        captured_at=now,
    )
    store = InMemoryScreenActivityObservationStore()
    backend = RecordingScreenBackend(
        ScreenActivityInferenceResult(
            screen_activity_label="debugging tests",
            confidence=0.84,
            model="unit-screen",
            app_hint="Terminal",
            raw_reason_json={},
        )
    )
    worker = ScreenActivityInferenceWorker(
        frame_store=frame_store,
        screen_activity_store=store,
        backend=backend,
    )

    result = await worker.process_once(now=now)

    assert result.processed_frame_id == latest_frame.id
    assert result.skipped_backlog_count == 1
    assert backend.frame_ids == [latest_frame.id]
    assert await store.fetch_by_frame(latest_frame.id) is not None
    assert await store.fetch_by_frame(old_frame.id) is None  # type: ignore[arg-type]


@pytest.mark.unit
def test_screen_activity_observation_rejects_empty_label() -> None:
    with pytest.raises(ValueError, match="screen_activity_label"):
        ScreenActivityObservation(
            frame_id=UUID("00000000-0000-0000-0000-000000000001"),
            observed_at=datetime(2026, 6, 16, 10, 0, tzinfo=UTC),
            screen_activity_label="",
            confidence=0.8,
            model="unit",
        )
