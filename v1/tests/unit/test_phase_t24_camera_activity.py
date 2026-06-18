from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from server.shared.perception import (
    HumanActivityObservation,
    InMemoryHumanActivityObservationStore,
    InMemoryHumanPresenceObservationStore,
    InMemoryPerceptionFrameStore,
)
from server.thinker.perception.activity import (
    HumanActivityInferenceResult,
    HumanActivityInferenceWorker,
    MlxVlmActivityBackend,
    coherent_activity_label,
    parse_activity_inference_json,
)


class RecordingActivityBackend:
    model = "unit-activity"

    def __init__(self, result: HumanActivityInferenceResult) -> None:
        self.result = result
        self.frame_ids: list[UUID] = []

    async def infer_activity(
        self,
        frame_path: str,
        *,
        frame_id: UUID,
    ) -> HumanActivityInferenceResult:
        del frame_path
        self.frame_ids.append(frame_id)
        return self.result


@pytest.mark.unit
def test_parse_activity_inference_json_validates_schema() -> None:
    parsed = parse_activity_inference_json(
        {"activity_label": "typing", "confidence": 0.72, "reason": "hands on keyboard"},
        model="gemma-e12b-activity",
    )

    assert parsed == HumanActivityInferenceResult(
        activity_label="typing",
        confidence=0.72,
        model="gemma-e12b-activity",
        raw_reason_json={"reason": "hands on keyboard"},
    )


@pytest.mark.unit
def test_parse_activity_inference_json_rejects_empty_label() -> None:
    with pytest.raises(ValueError, match="activity_label"):
        parse_activity_inference_json(
            {"activity_label": "", "confidence": 0.72},
            model="gemma-e12b-activity",
        )


@pytest.mark.unit
async def test_mlx_vlm_activity_backend_parses_json_from_image_generation() -> None:
    def fake_loader(model_name: str):
        assert model_name == "gemma-e12b-activity"
        return object(), object()

    def fake_stream_generator(model, processor, prompt, image, max_tokens):
        del model, processor, max_tokens
        assert "activity_label" in prompt
        assert image == "logs/perception/camera/frame.jpg"
        yield '{"activity_label": "reading", "confidence": 0.88, "reason": "book visible"}'

    backend = MlxVlmActivityBackend(
        model="gemma-e12b-activity",
        model_loader=fake_loader,
        stream_generator=fake_stream_generator,
    )

    result = await backend.infer_activity(
        "logs/perception/camera/frame.jpg",
        frame_id=UUID("00000000-0000-0000-0000-000000000001"),
    )

    assert result.activity_label == "reading"
    assert result.confidence == 0.88
    assert result.model == "gemma-e12b-activity"


@pytest.mark.unit
async def test_activity_worker_saves_observation_for_latest_frame() -> None:
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    frame_store = InMemoryPerceptionFrameStore()
    observation_store = InMemoryHumanActivityObservationStore()
    presence_store = InMemoryHumanPresenceObservationStore()
    frame = await frame_store.insert_frame(
        source="camera",
        file_path="logs/perception/camera/latest.jpg",
        sha256="latest",
        captured_at=now,
    )
    assert frame.id is not None
    presence = await presence_store.insert_observation(
        frame_id=frame.id,
        observed_at=now,
        present=True,
        confidence=0.9,
        model="unit-presence",
    )
    backend = RecordingActivityBackend(
        HumanActivityInferenceResult(
            activity_label="typing",
            confidence=0.84,
            model="unit-activity",
            raw_reason_json={"reason": "keyboard visible"},
        )
    )
    worker = HumanActivityInferenceWorker(
        frame_store=frame_store,
        activity_store=observation_store,
        presence_store=presence_store,
        backend=backend,
    )

    result = await worker.process_once(now=now)
    saved = await observation_store.fetch_by_frame(frame.id)

    assert result.processed_frame_id == frame.id
    assert result.skipped_backlog_count == 0
    assert saved is not None
    assert saved.presence_observation_id == presence.id
    assert saved.activity_label == "typing"


@pytest.mark.unit
async def test_activity_worker_skips_stale_and_backlog_frames() -> None:
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    frame_store = InMemoryPerceptionFrameStore()
    await frame_store.insert_frame(
        source="camera",
        file_path="logs/perception/camera/stale.jpg",
        sha256="stale",
        captured_at=now - timedelta(minutes=6),
    )
    older = await frame_store.insert_frame(
        source="camera",
        file_path="logs/perception/camera/older.jpg",
        sha256="older",
        captured_at=now - timedelta(seconds=10),
    )
    latest = await frame_store.insert_frame(
        source="camera",
        file_path="logs/perception/camera/latest.jpg",
        sha256="latest",
        captured_at=now,
    )
    observation_store = InMemoryHumanActivityObservationStore()
    backend = RecordingActivityBackend(
        HumanActivityInferenceResult(
            activity_label="idle",
            confidence=0.6,
            model="unit-activity",
            raw_reason_json={},
        )
    )
    worker = HumanActivityInferenceWorker(
        frame_store=frame_store,
        activity_store=observation_store,
        presence_store=None,
        backend=backend,
        stale_after=timedelta(minutes=5),
        backlog_limit=10,
    )

    result = await worker.process_once(now=now)

    assert result.processed_frame_id == latest.id
    assert result.skipped_stale_count == 1
    assert result.skipped_backlog_count == 1
    assert backend.frame_ids == [latest.id]
    assert await observation_store.fetch_by_frame(older.id) is None  # type: ignore[arg-type]


@pytest.mark.unit
def test_coherent_activity_label_rounds_presence_false_to_away() -> None:
    assert coherent_activity_label(present=False, activity_label="typing") == "away"
    assert coherent_activity_label(present=True, activity_label="typing") == "typing"
    assert coherent_activity_label(present=None, activity_label="typing") == "typing"


@pytest.mark.unit
def test_human_activity_observation_rejects_invalid_confidence() -> None:
    with pytest.raises(ValueError, match="confidence"):
        HumanActivityObservation(
            frame_id=UUID("00000000-0000-0000-0000-000000000001"),
            observed_at=datetime(2026, 6, 16, 10, 0, tzinfo=UTC),
            activity_label="typing",
            confidence=1.2,
            model="unit",
        )
