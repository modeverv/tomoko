from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from server.shared.perception import InMemoryPerceptionFrameStore, PerceptionFrame


@pytest.mark.unit
async def test_perception_frame_store_retains_latest_100_per_source() -> None:
    store = InMemoryPerceptionFrameStore()
    start = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    inserted: list[PerceptionFrame] = []
    for index in range(105):
        inserted.append(
            await store.insert_frame(
                source="camera",
                file_path=f"logs/perception/camera/{index:03d}.jpg",
                sha256=f"sha-{index:03d}",
                captured_at=start + timedelta(seconds=index),
                device_id="desk",
                width=640,
                height=480,
            )
        )

    retired_count = await store.apply_retention(source="camera", keep_latest=100)
    retained = await store.fetch_retained_frames(source="camera", limit=120)
    oldest = await store.fetch_frame(inserted[0].id)

    assert retired_count == 5
    assert len(retained) == 100
    assert retained[0].file_path.endswith("104.jpg")
    assert retained[-1].file_path.endswith("005.jpg")
    assert oldest is not None
    assert oldest.retained is False


@pytest.mark.unit
async def test_perception_frame_retention_is_source_scoped() -> None:
    store = InMemoryPerceptionFrameStore()
    start = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    camera_old = await store.insert_frame(
        source="camera",
        file_path="logs/perception/camera/old.jpg",
        sha256="camera-old",
        captured_at=start,
    )
    await store.insert_frame(
        source="camera",
        file_path="logs/perception/camera/new.jpg",
        sha256="camera-new",
        captured_at=start + timedelta(seconds=1),
    )
    screenshot = await store.insert_frame(
        source="screenshot",
        file_path="logs/perception/screenshot/old.png",
        sha256="screen-old",
        captured_at=start,
    )

    retired_count = await store.apply_retention(source="camera", keep_latest=1)

    assert retired_count == 1
    assert (await store.fetch_frame(camera_old.id)).retained is False  # type: ignore[union-attr]
    assert (await store.fetch_frame(screenshot.id)).retained is True  # type: ignore[union-attr]


@pytest.mark.unit
def test_perception_frame_rejects_unknown_source() -> None:
    with pytest.raises(ValueError, match="Unsupported perception frame source"):
        PerceptionFrame(
            source="microphone",  # type: ignore[arg-type]
            file_path="logs/perception/mic.raw",
            sha256="abc",
            captured_at=datetime(2026, 6, 16, 10, 0, tzinfo=UTC),
        )
