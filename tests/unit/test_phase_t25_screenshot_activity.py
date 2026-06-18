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
    ScreenMetadata,
    ScreenshotCaptureWorker,
    parse_screen_activity_inference_json,
    parse_screen_activity_inference_lines,
    parse_screen_activity_inference_output,
    refine_screen_activity_with_evidence,
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

    assert parsed.screen_activity_label == "debugging tests"
    assert parsed.confidence == 0.81
    assert parsed.model == "gemma-e12b-screen"
    assert parsed.app_hint == "Terminal"
    assert parsed.document_hint == "pytest"
    assert parsed.url_hint is None
    assert parsed.raw_reason_json["reason"] == "pytest output visible"
    assert parsed.raw_reason_json["visual_coarse_signal"] == {
        "watching_video": False,
        "playing_game": False,
        "coding_or_terminal": False,
        "reading_document_or_web": False,
        "reviewing_report_or_logs": False,
        "communication_app": False,
    }


@pytest.mark.unit
def test_parse_screen_activity_inference_lines_reads_fixed_structured_output() -> None:
    parsed = parse_screen_activity_inference_lines(
        """
        SCREEN_ACTIVITY_LABEL=reviewing report
        CONFIDENCE=0.88
        APP_HINT=Codex
        DOCUMENT_HINT=thinker2 inspection
        URL_HINT=none
        WATCHING_VIDEO=0
        PLAYING_GAME=0
        CODING_OR_TERMINAL=1
        READING_DOCUMENT_OR_WEB=1
        REVIEWING_REPORT_OR_LOGS=1
        COMMUNICATION_APP=0
        REASON=OCR and report UI are visible
        """,
        model="qwen-vl",
    )

    assert parsed.screen_activity_label == "reviewing report"
    assert parsed.confidence == 0.88
    assert parsed.app_hint == "Codex"
    assert parsed.document_hint == "thinker2 inspection"
    assert parsed.url_hint is None
    assert parsed.raw_reason_json["visual_coarse_signal"] == {
        "watching_video": False,
        "playing_game": False,
        "coding_or_terminal": True,
        "reading_document_or_web": True,
        "reviewing_report_or_logs": True,
        "communication_app": False,
    }


@pytest.mark.unit
def test_parse_screen_activity_inference_output_falls_back_to_json() -> None:
    parsed = parse_screen_activity_inference_output(
        '{"screen_activity_label": "reading docs", "confidence": 0.72}',
        model="qwen-vl",
    )

    assert parsed.screen_activity_label == "reading docs"
    assert parsed.confidence == 0.72


@pytest.mark.unit
async def test_mlx_vlm_screen_backend_parses_json_from_image_generation() -> None:
    def fake_loader(model_name: str):
        assert model_name == "gemma-e12b-screen"
        return object(), object()

    def fake_stream_generator(model, processor, prompt, image, max_tokens):
        del model, processor
        assert max_tokens == 100
        assert "SCREEN_ACTIVITY_LABEL=" in prompt
        assert "WATCHING_VIDEO=<0 or 1>" in prompt
        assert "PLAYING_GAME=<0 or 1>" in prompt
        assert "REVIEWING_REPORT_OR_LOGS=<0 or 1>" in prompt
        assert "Do not write a long summary" in prompt
        assert "OCR_TEXT:" in prompt
        assert "thinker2 inspection" in prompt
        assert image == "logs/perception/screenshot/frame.png"
        yield (
            "SCREEN_ACTIVITY_LABEL=reading docs\n"
            "CONFIDENCE=0.9\n"
            "APP_HINT=none\n"
            "DOCUMENT_HINT=none\n"
            "URL_HINT=none\n"
            "WATCHING_VIDEO=0\n"
            "PLAYING_GAME=0\n"
            "CODING_OR_TERMINAL=0\n"
            "READING_DOCUMENT_OR_WEB=1\n"
            "REVIEWING_REPORT_OR_LOGS=1\n"
            "COMMUNICATION_APP=0\n"
            "REASON=report text visible\n"
        )

    backend = MlxVlmScreenActivityBackend(
        model="gemma-e12b-screen",
        model_loader=fake_loader,
        stream_generator=fake_stream_generator,
        ocr_text_provider=lambda path: "thinker2 inspection\nmake thinker2-capture-once",
        metadata_provider=lambda: ScreenMetadata(front_app="Terminal"),
    )

    result = await backend.infer_screen_activity(
        "logs/perception/screenshot/frame.png",
        frame_id=UUID("00000000-0000-0000-0000-000000000001"),
    )

    assert result.screen_activity_label == "reviewing thinker2 inspection and capture output"
    assert result.confidence == 0.95
    assert result.app_hint == "Terminal"
    assert result.raw_reason_json["task_context"] == (
        "validating thinker2 perception capture and screen interpretation"
    )
    assert result.raw_reason_json["vlm_screen_activity_label"] == "reading docs"


@pytest.mark.unit
async def test_mlx_vlm_screen_backend_falls_back_to_ocr_when_long_json_breaks() -> None:
    def fake_loader(model_name: str):
        assert model_name == "qwen-vl"
        return object(), object()

    def fake_stream_generator(model, processor, prompt, image, max_tokens):
        del model, processor, prompt, image, max_tokens
        yield "The screen shows thinker2 output, but this is not JSON."

    backend = MlxVlmScreenActivityBackend(
        model="qwen-vl",
        model_loader=fake_loader,
        stream_generator=fake_stream_generator,
        ocr_text_provider=lambda path: "thinker2_context make thinker2-capture-once",
        metadata_provider=lambda: ScreenMetadata(
            front_app="Codex",
            chrome_title="thinker2 inspection",
        ),
    )

    result = await backend.infer_screen_activity(
        "logs/perception/screenshot/frame.png",
        frame_id=UUID("00000000-0000-0000-0000-000000000001"),
    )

    assert result.screen_activity_label == "reviewing thinker2 inspection and capture output"
    assert result.raw_reason_json["reason"] == "vlm_json_parse_failed"
    assert result.raw_reason_json["vlm_raw_output_prefix"] == (
        "The screen shows thinker2 output, but this is not JSON."
    )
    assert result.raw_reason_json["vlm_screen_activity_label"] == "unknown screen activity"


@pytest.mark.unit
def test_refine_screen_activity_with_evidence_prefers_ocr_over_generic_vlm_label() -> None:
    result = refine_screen_activity_with_evidence(
        ScreenActivityInferenceResult(
            screen_activity_label="Clicking on the 'Home' button",
            confidence=0.9,
            model="qwen-vl",
            app_hint="Home",
            raw_reason_json={"reason": "The User is interacting with the home screen"},
        ),
        ocr_text="thinker2_context captured_frames make thinker2-capture-once",
        metadata=ScreenMetadata(
            front_app="Codex",
            front_window_title="Codex",
            chrome_title="thinker2 latest inspection",
            chrome_url="file:///Users/seijiro/Sync/sync_work/by-llms/tomoko/reports/thinker2/latest.html",
        ),
    )

    assert result.screen_activity_label == "reviewing thinker2 inspection and capture output"
    assert result.app_hint == "Codex"
    assert result.document_hint == "thinker2 latest inspection"
    assert result.url_hint == (
        "file:///Users/seijiro/Sync/sync_work/by-llms/tomoko/reports/thinker2/latest.html"
    )
    assert result.raw_reason_json["refined_by"] == "ocr_os_metadata"
    assert "Chrome URL is file:///Users" in str(result.raw_reason_json["summary"])
    assert result.raw_reason_json["task_context"] == (
        "validating thinker2 perception capture and screen interpretation"
    )
    assert result.raw_reason_json["vlm_screen_activity_label"] == "Clicking on the 'Home' button"


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
