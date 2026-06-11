"""Structured JSONL logging for turn-taking v1 main decisions and v2 shadow advisories."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

_log_dir: str | None = None
_main_log: Path | None = None
_v2_log: Path | None = None


def _ensure_dir() -> None:
    global _log_dir
    if _log_dir is None:
        _log_dir = os.environ.get(
            "TURN_TAKING_LOG_DIR",
            str(Path(__file__).resolve().parents[2] / "logs"),
        )
    dir_path = Path(_log_dir)
    dir_path.mkdir(parents=True, exist_ok=True)


def _main_log_path() -> Path:
    global _main_log
    if _main_log is None:
        _ensure_dir()
        _main_log = Path(_log_dir) / "turn-taking-main.jsonl"  # type: ignore[arg-type]
    return _main_log


def _v2_log_path() -> Path:
    global _v2_log
    if _v2_log is None:
        _ensure_dir()
        _v2_log = Path(_log_dir) / "turn-taking-v2-shadow.jsonl"  # type: ignore[arg-type]
    return _v2_log


def _serialize(obj: Any) -> Any:
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, float):
        return round(obj, 4)
    return obj


def _write(path: Path, record: dict[str, Any]) -> None:
    try:
        serialized = {k: _serialize(v) for k, v in record.items()}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(serialized, ensure_ascii=False) + "\n")
    except Exception:
        pass


def log_main_decision(
    *,
    ts_ms: int,
    conversation_session_id: UUID | None,
    turn_id: UUID | None,
    decision: str,
    reason: str,
    text: str,
    source: str,
    elapsed_ms: float,
    pending_reply_state: str,
    playback_state: str,
) -> None:
    record = {
        "ts_ms": ts_ms,
        "conversation_session_id": conversation_session_id,
        "turn_id": turn_id,
        "lane": "main",
        "event": "final_transcript_received",
        "text": text,
        "decision": decision,
        "reason": reason,
        "source": source,
        "elapsed_ms": round(elapsed_ms, 2),
        "pending_reply_state": pending_reply_state,
        "playback_state": playback_state,
    }
    _write(_main_log_path(), record)


def log_v2_shadow_advisory(
    *,
    ts_ms: int,
    conversation_session_id: UUID | None,
    turn_id: UUID | None,
    partial_revision: int,
    stable_text: str | None,
    semantic_saturation: float | None,
    remaining_info_risk: float | None,
    semantic_split_risk: float | None,
    speech_decision_score: float | None,
    proposal: str | None,
    confidence: float | None,
    would_start_inference: bool | None,
    reason: str | None,
) -> None:
    record = {
        "ts_ms": ts_ms,
        "conversation_session_id": conversation_session_id,
        "turn_id": turn_id,
        "lane": "v2_shadow",
        "event": "speech_decision_score",
        "partial_revision": partial_revision,
        "stable_text": stable_text,
        "semantic_saturation": semantic_saturation,
        "remaining_info_risk": remaining_info_risk,
        "semantic_split_risk": semantic_split_risk,
        "speech_decision_score": speech_decision_score,
        "proposal": proposal,
        "confidence": confidence,
        "would_start_inference": would_start_inference,
        "reason": reason,
    }
    _write(_v2_log_path(), record)


def log_provisional_inference_event(
    *,
    ts_ms: int,
    conversation_session_id: UUID | None,
    turn_id: UUID | None,
    event: str,
    text: str | None,
    reason: str,
) -> None:
    record = {
        "ts_ms": ts_ms,
        "conversation_session_id": conversation_session_id,
        "turn_id": turn_id,
        "lane": "main",
        "event": event,
        "text": text or "",
        "reason": reason,
    }
    _write(_main_log_path(), record)


def log_provisional_inference_start(
    *,
    ts_ms: int,
    conversation_session_id: UUID | None,
    turn_id: UUID | None,
    stable_text: str | None,
    reason: str,
) -> None:
    log_provisional_inference_event(
        ts_ms=ts_ms,
        conversation_session_id=conversation_session_id,
        turn_id=turn_id,
        event="provisional_inference_start",
        text=stable_text,
        reason=reason,
    )
