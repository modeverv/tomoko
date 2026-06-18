from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from server.thinker.perception.os_capture import (
    CommandResult,
    MacOSScreenshotCaptureProvider,
    OpenCVCameraCaptureProvider,
)


@pytest.mark.unit
async def test_macos_screenshot_provider_invokes_screencapture(tmp_path: Path) -> None:
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    commands: list[tuple[str, ...]] = []

    async def fake_runner(command: tuple[str, ...]) -> CommandResult:
        commands.append(command)
        Image.new("RGB", (320, 200), color=(10, 20, 30)).save(command[-1])
        return CommandResult(returncode=0, stdout="", stderr="")

    provider = MacOSScreenshotCaptureProvider(
        output_dir=tmp_path,
        command_runner=fake_runner,
    )

    artifact = await provider.capture(captured_at=now)

    assert commands == [
        (
            "/usr/sbin/screencapture",
            "-x",
            "-t",
            "png",
            str(tmp_path / "20260616T100000000000Z-screenshot.png"),
        )
    ]
    assert artifact.file_path.endswith("-screenshot.png")
    assert artifact.width == 320
    assert artifact.height == 200
    assert len(artifact.sha256) == 64


@pytest.mark.unit
async def test_opencv_camera_provider_writes_camera_frame(tmp_path: Path) -> None:
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    captured_paths: list[Path] = []

    def fake_capture(
        path: Path,
        camera_index: int,
        warmup_frames: int,
        warmup_delay_sec: float,
    ) -> None:
        assert camera_index == 1
        assert warmup_frames == 12
        assert warmup_delay_sec == 0.01
        captured_paths.append(path)
        Image.new("RGB", (640, 480), color=(30, 20, 10)).save(path)

    provider = OpenCVCameraCaptureProvider(
        output_dir=tmp_path,
        camera_index=1,
        warmup_frames=12,
        warmup_delay_sec=0.01,
        capture_file=fake_capture,
    )

    artifact = await provider.capture(captured_at=now)

    assert captured_paths == [tmp_path / "20260616T100000000000Z-camera.jpg"]
    assert artifact.file_path.endswith("-camera.jpg")
    assert artifact.width == 640
    assert artifact.height == 480
    assert len(artifact.sha256) == 64
