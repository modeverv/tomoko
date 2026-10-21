from __future__ import annotations

import json
import subprocess
from pathlib import Path
from shutil import which

ROOT = Path(__file__).resolve().parents[2]
CAMERA_SOURCE = ROOT / "scripts" / "camera_presence" / "CameraPresence.swift"
CAMERA_PLIST = ROOT / "scripts" / "camera_presence" / "Info.plist"
CAMERA_APP = ROOT / ".cache" / "tomoko" / "CameraPresence.app"
CAMERA_BINARY = CAMERA_APP / "Contents" / "MacOS" / "camera-presence"


def ensure_camera_presence_command() -> Path:
    if CAMERA_BINARY.exists() and not _needs_rebuild():
        return CAMERA_BINARY
    if not CAMERA_SOURCE.exists():
        raise RuntimeError(f"camera presence source is missing: {CAMERA_SOURCE}")
    if which("swiftc") is None:
        raise RuntimeError("swiftc is required to build the camera presence sidecar")
    CAMERA_BINARY.parent.mkdir(parents=True, exist_ok=True)
    (CAMERA_APP / "Contents").mkdir(parents=True, exist_ok=True)
    if CAMERA_PLIST.exists():
        import shutil

        shutil.copy2(CAMERA_PLIST, CAMERA_APP / "Contents" / "Info.plist")
    subprocess.run(
        [
            "swiftc",
            "-O",
            str(CAMERA_SOURCE),
            "-framework",
            "AVFoundation",
            "-framework",
            "Vision",
            "-framework",
            "CoreMedia",
            "-Xlinker",
            "-sectcreate",
            "-Xlinker",
            "__TEXT",
            "-Xlinker",
            "__info_plist",
            "-Xlinker",
            str(CAMERA_PLIST),
            "-o",
            str(CAMERA_BINARY),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if which("codesign") is not None:
        subprocess.run(
            ["codesign", "--force", "--sign", "-", str(CAMERA_BINARY)],
            check=True,
            capture_output=True,
            text=True,
        )
    return CAMERA_BINARY


def camera_presence_once(*, timeout_sec: float = 10.0) -> dict[str, object] | None:
    """カメラ1フレームで在/不在を判定する。失敗時は None。"""
    try:
        command = ensure_camera_presence_command()
    except (RuntimeError, subprocess.CalledProcessError):
        return None
    try:
        completed = subprocess.run(
            [str(command), "--timeout", str(timeout_sec)],
            capture_output=True,
            text=True,
            timeout=timeout_sec + 5.0,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    return dict(payload)


def camera_presence_available() -> bool:
    return CAMERA_BINARY.exists() or (
        CAMERA_SOURCE.exists() and which("swiftc") is not None
    )


def _needs_rebuild() -> bool:
    binary_mtime = CAMERA_BINARY.stat().st_mtime
    return (
        CAMERA_SOURCE.exists()
        and CAMERA_SOURCE.stat().st_mtime > binary_mtime
        or CAMERA_PLIST.exists()
        and CAMERA_PLIST.stat().st_mtime > binary_mtime
    )
