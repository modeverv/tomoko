from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from server.shared.perception import (
    HumanPresenceObservation,
    InMemoryHumanPresenceObservationStore,
    InMemoryPerceptionFrameStore,
)
from server.thinker.perception.presence import (
    CameraCaptureWorker,
    CapturedFrameArtifact,
    HumanPresenceInferenceResult,
    MlxVlmPresenceBackend,
    PresenceInferenceWorker,
    parse_presence_inference_json,
)


class StaticCaptureProvider:
    def __init__(self, artifact: CapturedFrameArtifact) -> None:
        self.artifact = artifact
        self.calls = 0

    async def capture(self, *, captured_at: datetime) -> CapturedFrameArtifact:
        self.calls += 1
        assert captured_at == self.artifact.captured_at
        return self.artifact


class RecordingPresenceBackend:
    model = "unit-presence"

    def __init__(self, result: HumanPresenceInferenceResult) -> None:
        self.result = result
        self.frame_ids: list[UUID] = []

    async def infer_presence(
        self,
        frame_path: str,
        *,
        frame_id: UUID,
    ) -> HumanPresenceInferenceResult:
        del frame_path
        self.frame_ids.append(frame_id)
        return self.result


@pytest.mark.unit
def test_parse_presence_inference_json_validates_schema() -> None:
    parsed = parse_presence_inference_json(
        {"present": True, "confidence": 0.82, "reason": "desk visible"},
        model="gemma-e12b-presence",
    )

    assert parsed == HumanPresenceInferenceResult(
        present=True,
        confidence=0.82,
        model="gemma-e12b-presence",
        raw_reason_json={"reason": "desk visible"},
    )


@pytest.mark.unit
def test_parse_presence_inference_json_rejects_invalid_confidence() -> None:
    with pytest.raises(ValueError, match="confidence"):
        parse_presence_inference_json(
            {"present": True, "confidence": 1.2},
            model="gemma-e12b-presence",
        )


@pytest.mark.unit
async def test_mlx_vlm_presence_backend_parses_json_from_image_generation() -> None:
    def fake_loader(model_name: str):
        assert model_name == "gemma-e12b-presence"
        return object(), object()

    def fake_stream_generator(model, processor, prompt, image, max_tokens):
        del model, processor, max_tokens
        assert "present" in prompt
        assert image == "logs/perception/camera/frame.jpg"
        yield '{"present": true, "confidence": 0.93, "reason": "person visible"}'

    backend = MlxVlmPresenceBackend(
        model="gemma-e12b-presence",
        model_loader=fake_loader,
        stream_generator=fake_stream_generator,
    )

    result = await backend.infer_presence(
        "logs/perception/camera/frame.jpg",
        frame_id=UUID("00000000-0000-0000-0000-000000000001"),
    )

    assert result.present is True
    assert result.confidence == 0.93
    assert result.model == "gemma-e12b-presence"
    assert result.raw_reason_json == {"reason": "person visible"}


@pytest.mark.unit
async def test_camera_capture_worker_saves_camera_frame_and_applies_retention() -> None:
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    frame_store = InMemoryPerceptionFrameStore()
    provider = StaticCaptureProvider(
        CapturedFrameArtifact(
            file_path="logs/perception/camera/frame.jpg",
            sha256="sha-camera",
            captured_at=now,
            width=640,
            height=480,
        )
    )
    worker = CameraCaptureWorker(
        frame_store=frame_store,
        provider=provider,
        device_id="desk",
        retention_limit=1,
    )

    frame = await worker.capture_once(now=now)

    assert frame.source == "camera"
    assert frame.device_id == "desk"
    assert frame.file_path.endswith("frame.jpg")
    assert provider.calls == 1
    assert len(await frame_store.fetch_retained_frames(source="camera", limit=10)) == 1


@pytest.mark.unit
async def test_presence_worker_processes_latest_unprocessed_frame_and_skips_backlog() -> None:
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    frame_store = InMemoryPerceptionFrameStore()
    old_frame = await frame_store.insert_frame(
        source="camera",
        file_path="logs/perception/camera/old.jpg",
        sha256="old",
        captured_at=now - timedelta(seconds=20),
    )
    latest_frame = await frame_store.insert_frame(
        source="camera",
        file_path="logs/perception/camera/latest.jpg",
        sha256="latest",
        captured_at=now,
    )
    observation_store = InMemoryHumanPresenceObservationStore()
    backend = RecordingPresenceBackend(
        HumanPresenceInferenceResult(
            present=True,
            confidence=0.91,
            model="unit-presence",
            raw_reason_json={"reason": "person visible"},
        )
    )
    worker = PresenceInferenceWorker(
        frame_store=frame_store,
        observation_store=observation_store,
        backend=backend,
        stale_after=timedelta(minutes=5),
        backlog_limit=10,
    )

    result = await worker.process_once(now=now)

    assert result.processed_frame_id == latest_frame.id
    assert result.skipped_backlog_count == 1
    assert result.skipped_stale_count == 0
    assert backend.frame_ids == [latest_frame.id]
    assert await observation_store.fetch_by_frame(latest_frame.id) is not None
    assert await observation_store.fetch_by_frame(old_frame.id) is None


@pytest.mark.unit
async def test_presence_worker_discards_stale_frames() -> None:
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    frame_store = InMemoryPerceptionFrameStore()
    await frame_store.insert_frame(
        source="camera",
        file_path="logs/perception/camera/stale.jpg",
        sha256="stale",
        captured_at=now - timedelta(minutes=6),
    )
    observation_store = InMemoryHumanPresenceObservationStore()
    backend = RecordingPresenceBackend(
        HumanPresenceInferenceResult(
            present=False,
            confidence=0.8,
            model="unit-presence",
            raw_reason_json={},
        )
    )
    worker = PresenceInferenceWorker(
        frame_store=frame_store,
        observation_store=observation_store,
        backend=backend,
        stale_after=timedelta(minutes=5),
    )

    result = await worker.process_once(now=now)

    assert result.processed_frame_id is None
    assert result.skipped_stale_count == 1
    assert backend.frame_ids == []


@pytest.mark.unit
def test_human_presence_observation_rejects_invalid_confidence() -> None:
    with pytest.raises(ValueError, match="confidence"):
        HumanPresenceObservation(
            frame_id=UUID("00000000-0000-0000-0000-000000000001"),
            observed_at=datetime(2026, 6, 16, 10, 0, tzinfo=UTC),
            present=True,
            confidence=-0.1,
            model="unit",
        )
