from __future__ import annotations

import asyncio
import hashlib
import subprocess
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image

from server.thinker.perception.presence import CapturedFrameArtifact
from server.thinker.perception.screen import CapturedScreenshotArtifact


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[tuple[str, ...]], Awaitable[CommandResult]]
CameraCaptureFile = Callable[[Path, int, int, float], None]


class MacOSScreenshotCaptureProvider:
    def __init__(
        self,
        *,
        output_dir: Path | str = "logs/perception/screenshot",
        command_runner: CommandRunner | None = None,
        command: str = "/usr/sbin/screencapture",
    ) -> None:
        self.output_dir = Path(output_dir)
        self.command_runner = command_runner or run_command
        self.command = command

    async def capture(self, *, captured_at: datetime) -> CapturedScreenshotArtifact:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"{_timestamp_slug(captured_at)}-screenshot.png"
        command = (self.command, "-x", "-t", "png", str(path))
        result = await self.command_runner(command)
        if result.returncode != 0:
            raise RuntimeError(
                "screencapture failed "
                f"returncode={result.returncode} stderr={result.stderr.strip()!r}"
            )
        return _screenshot_artifact_from_file(path, captured_at=captured_at)


class OpenCVCameraCaptureProvider:
    def __init__(
        self,
        *,
        output_dir: Path | str = "logs/perception/camera",
        camera_index: int = 0,
        warmup_frames: int = 30,
        warmup_delay_sec: float = 0.05,
        capture_file: CameraCaptureFile | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.camera_index = camera_index
        self.warmup_frames = warmup_frames
        self.warmup_delay_sec = warmup_delay_sec
        self.capture_file = capture_file or capture_opencv_camera_file

    async def capture(self, *, captured_at: datetime) -> CapturedFrameArtifact:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"{_timestamp_slug(captured_at)}-camera.jpg"
        self.capture_file(
            path,
            self.camera_index,
            self.warmup_frames,
            self.warmup_delay_sec,
        )
        return _camera_artifact_from_file(path, captured_at=captured_at)


async def run_command(command: Sequence[str]) -> CommandResult:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return CommandResult(
        returncode=process.returncode,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
    )


def capture_opencv_camera_file(
    path: Path,
    camera_index: int,
    warmup_frames: int,
    warmup_delay_sec: float,
) -> None:
    import cv2

    capture = cv2.VideoCapture(camera_index)
    try:
        if not capture.isOpened():
            raise RuntimeError(f"camera index {camera_index} could not be opened")
        frame = None
        frames_to_read = max(1, warmup_frames)
        for _ in range(frames_to_read):
            ok, next_frame = capture.read()
            if ok and next_frame is not None:
                frame = next_frame
            if warmup_delay_sec > 0:
                time.sleep(warmup_delay_sec)
        if frame is None:
            raise RuntimeError(f"camera index {camera_index} did not return a frame")
        if not cv2.imwrite(str(path), frame):
            raise RuntimeError(f"failed to write camera frame to {path}")
    finally:
        capture.release()


def _camera_artifact_from_file(
    path: Path,
    *,
    captured_at: datetime,
) -> CapturedFrameArtifact:
    width, height = _image_size(path)
    return CapturedFrameArtifact(
        file_path=str(path),
        sha256=_sha256_file(path),
        captured_at=captured_at,
        width=width,
        height=height,
    )


def _screenshot_artifact_from_file(
    path: Path,
    *,
    captured_at: datetime,
) -> CapturedScreenshotArtifact:
    width, height = _image_size(path)
    return CapturedScreenshotArtifact(
        file_path=str(path),
        sha256=_sha256_file(path),
        captured_at=captured_at,
        width=width,
        height=height,
    )


def _timestamp_slug(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%S%fZ")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size
