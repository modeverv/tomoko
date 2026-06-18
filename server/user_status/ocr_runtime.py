from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from shutil import which

from server.shared.models import utc_now
from server.user_status.main import OSMetadata, build_user_status_observation

ROOT = Path(__file__).resolve().parents[2]
VISION_OCR_SOURCE = ROOT / "scripts" / "vision_ocr" / "VisionOCR.swift"
VISION_OCR_BINARY = ROOT / ".cache" / "tomoko" / "vision-ocr"


@dataclass(frozen=True, slots=True)
class OcrRuntimeResult:
    screenshot_path: Path
    text: str
    metadata: OSMetadata
    activity_label: str
    present: bool


def capture_screen(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["screencapture", "-x", str(path)],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and path.exists()


def tesseract_ocr_text(path: Path, *, language: str = "eng+jpn") -> str:
    if not path.exists():
        return ""
    try:
        result = subprocess.run(
            ["tesseract", str(path), "stdout", "-l", language, "--psm", "6"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def vision_ocr_text(path: Path, *, languages: tuple[str, ...] = ("ja-JP", "en-US")) -> str:
    if not path.exists():
        return ""
    try:
        command = ensure_vision_ocr_command()
    except RuntimeError:
        return ""
    args = [str(command), "--image", str(path)]
    for language in languages:
        args.extend(["--language", language])
    try:
        result = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return ""
    return str(payload.get("text", "")).strip()


def ensure_vision_ocr_command() -> Path:
    if VISION_OCR_BINARY.exists() and not _vision_ocr_needs_rebuild():
        return VISION_OCR_BINARY
    if not VISION_OCR_SOURCE.exists():
        raise RuntimeError(f"Vision OCR source is missing: {VISION_OCR_SOURCE}")
    if which("swiftc") is None:
        raise RuntimeError("swiftc is required to build the Vision OCR sidecar")
    VISION_OCR_BINARY.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "swiftc",
            "-O",
            str(VISION_OCR_SOURCE),
            "-framework",
            "Vision",
            "-framework",
            "ImageIO",
            "-o",
            str(VISION_OCR_BINARY),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if which("codesign") is not None:
        subprocess.run(
            ["codesign", "--force", "--sign", "-", str(VISION_OCR_BINARY)],
            check=True,
            capture_output=True,
            text=True,
        )
    return VISION_OCR_BINARY


def macos_front_metadata() -> OSMetadata:
    front_app = _osascript(
        'tell application "System Events" to get name of first application process '
        "whose frontmost is true"
    )
    front_title = ""
    if front_app:
        front_title = _osascript(
            f'tell application "System Events" to tell process "{_escape(front_app)}" '
            "to get name of front window"
        )
    chrome_title = _osascript(
        'tell application "Google Chrome" to get title of active tab of front window'
    )
    chrome_url = _osascript(
        'tell application "Google Chrome" to get URL of active tab of front window'
    )
    return OSMetadata(
        app_name=front_app or None,
        window_title=chrome_title or front_title or None,
        url=chrome_url or None,
    )


def capture_ocr_observation_once(
    *,
    artifact_dir: Path = Path("logs/user-status"),
    present: bool = True,
) -> OcrRuntimeResult:
    path = artifact_dir / f"{utc_now().strftime('%Y%m%dT%H%M%S%fZ')}-screen.png"
    captured = capture_screen(path)
    text = ocr_text(path) if captured else ""
    metadata = macos_front_metadata()
    observation = build_user_status_observation(
        present=present,
        ocr_text=text,
        metadata=metadata,
        artifact_path=str(path) if captured else None,
    )
    return OcrRuntimeResult(
        screenshot_path=path,
        text=text,
        metadata=metadata,
        activity_label=observation.activity_label,
        present=observation.present,
    )


def ocr_runtime_available() -> dict[str, bool]:
    return {
        "screencapture": _command_exists("screencapture"),
        "vision_ocr": VISION_OCR_BINARY.exists()
        or (VISION_OCR_SOURCE.exists() and _command_exists("swiftc")),
        "tesseract": _command_exists("tesseract"),
        "osascript": _command_exists("osascript"),
    }


def ocr_text(path: Path) -> str:
    return vision_ocr_text(path) or tesseract_ocr_text(path)


def _command_exists(command: str) -> bool:
    return which(command) is not None


def _osascript(script: str) -> str:
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _vision_ocr_needs_rebuild() -> bool:
    return (
        VISION_OCR_SOURCE.exists()
        and VISION_OCR_BINARY.exists()
        and VISION_OCR_SOURCE.stat().st_mtime > VISION_OCR_BINARY.stat().st_mtime
    )
