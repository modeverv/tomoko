from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from server.shared.models import UserStatusObservation, utc_now


@dataclass(frozen=True, slots=True)
class OSMetadata:
    app_name: str | None = None
    window_title: str | None = None
    url: str | None = None


def infer_activity_from_text(ocr_text: str, metadata: OSMetadata) -> str:
    evidence = " ".join(
        item.lower()
        for item in [
            ocr_text,
            metadata.app_name or "",
            metadata.window_title or "",
            metadata.url or "",
        ]
    )
    if "youtube" in evidence or "watch" in evidence:
        return "watching_video"
    if "codex" in evidence or "pytest" in evidence or "terminal" in evidence:
        return "coding_or_terminal"
    if "calendar" in evidence:
        return "checking_calendar"
    return "unknown_activity"


def build_user_status_observation(
    *,
    present: bool,
    ocr_text: str,
    metadata: OSMetadata,
    artifact_path: str | None,
) -> UserStatusObservation:
    activity = infer_activity_from_text(ocr_text, metadata)
    summary = f"{activity}: {ocr_text[:120]}".strip()
    return UserStatusObservation(
        present=present,
        activity_label=activity,
        summary=summary,
        confidence=0.8 if activity != "unknown_activity" else 0.3,
        visible_text=ocr_text,
        app_name=metadata.app_name,
        window_title=metadata.window_title,
        url=metadata.url,
        artifact_path=artifact_path,
        source="ocr_os_metadata",
    )


@dataclass(slots=True)
class ArtifactRetention:
    directory: Path
    retention_sec: int

    def prune(self) -> list[Path]:
        now = utc_now().timestamp()
        removed: list[Path] = []
        if not self.directory.exists():
            return removed
        for path in self.directory.iterdir():
            if path.is_file() and now - path.stat().st_mtime > self.retention_sec:
                path.unlink()
                removed.append(path)
        return removed
