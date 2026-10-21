from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

from server.shared.models import UserStatusObservation
from server.tomoko.sense import (
    SENSE_KIND_CAMERA_PRESENCE,
    SENSE_KIND_SCREENSHOT,
    SENSE_STATUS_FAILED,
    claim_pending_sense_requests,
    complete_sense_request_sql,
    insert_user_status_observation_sql,
)
from server.user_status.camera import camera_presence_once
from server.user_status.main import build_user_status_observation
from server.user_status.ocr_runtime import OcrRuntimeResult, capture_ocr_observation_once

_last_periodic_capture_monotonic: float = 0.0
_last_camera_presence_monotonic: float = 0.0


@dataclass(slots=True)
class UserStatusTickResult:
    requests_claimed: int = 0
    requests_done: int = 0
    requests_failed: int = 0
    periodic_captured: bool = False


async def process_screenshot_sense_requests(
    conn: Any,
    *,
    capture: Callable[[], OcrRuntimeResult] | None = None,
    limit: int = 2,
) -> UserStatusTickResult:
    requests = await claim_pending_sense_requests(
        conn,
        kind=SENSE_KIND_SCREENSHOT,
        limit=limit,
    )
    result = UserStatusTickResult(requests_claimed=len(requests))
    for request in requests:
        try:
            runtime_result = await asyncio.to_thread(
                capture or capture_ocr_observation_once
            )
        except Exception as exc:
            result.requests_failed += 1
            command = complete_sense_request_sql(
                request.id,
                result={"error": type(exc).__name__, "message": str(exc)},
                status=SENSE_STATUS_FAILED,
            )
            await conn.execute(command.query, command.params)
            continue
        observation = _observation_from_runtime_result(runtime_result)
        observation.trace_id = request.trace_id
        await _insert_observation(conn, observation)
        command = complete_sense_request_sql(
            request.id,
            result=_sense_result_payload(observation),
        )
        await conn.execute(command.query, command.params)
        result.requests_done += 1
    return result


async def process_camera_presence_sense_requests(
    conn: Any,
    *,
    presence: Callable[[], dict[str, Any] | None] | None = None,
    limit: int = 2,
) -> UserStatusTickResult:
    requests = await claim_pending_sense_requests(
        conn,
        kind=SENSE_KIND_CAMERA_PRESENCE,
        limit=limit,
    )
    result = UserStatusTickResult(requests_claimed=len(requests))
    for request in requests:
        payload = await asyncio.to_thread(presence or camera_presence_once)
        if payload is None:
            result.requests_failed += 1
            command = complete_sense_request_sql(
                request.id,
                result={"error": "camera_unavailable"},
                status=SENSE_STATUS_FAILED,
            )
            await conn.execute(command.query, command.params)
            continue
        observation = _observation_from_camera_payload(payload)
        observation.trace_id = request.trace_id
        await _insert_observation(conn, observation)
        command = complete_sense_request_sql(request.id, result=dict(payload))
        await conn.execute(command.query, command.params)
        result.requests_done += 1
    return result


async def maybe_capture_periodic_camera_presence(
    conn: Any,
    *,
    interval_sec: float,
    presence: Callable[[], dict[str, Any] | None] | None = None,
) -> bool:
    global _last_camera_presence_monotonic
    if interval_sec <= 0:
        return False
    now = monotonic()
    if now - _last_camera_presence_monotonic < interval_sec:
        return False
    _last_camera_presence_monotonic = now
    payload = await asyncio.to_thread(presence or camera_presence_once)
    if payload is None:
        return False
    await _insert_observation(conn, _observation_from_camera_payload(payload))
    return True


def _observation_from_camera_payload(payload: dict[str, Any]) -> UserStatusObservation:
    present = bool(payload.get("present", False))
    return UserStatusObservation(
        present=present,
        activity_label="camera_presence",
        summary="camera: user is present" if present else "camera: user is absent",
        source="camera",
        confidence=float(payload.get("confidence", 0.0)),
    )


async def maybe_capture_periodic_user_status(
    conn: Any,
    *,
    interval_sec: float,
    capture: Callable[[], OcrRuntimeResult] | None = None,
) -> bool:
    global _last_periodic_capture_monotonic
    if interval_sec <= 0:
        return False
    now = monotonic()
    if now - _last_periodic_capture_monotonic < interval_sec:
        return False
    _last_periodic_capture_monotonic = now
    runtime_result = await asyncio.to_thread(capture or capture_ocr_observation_once)
    observation = _observation_from_runtime_result(runtime_result)
    await _insert_observation(conn, observation)
    return True


def _observation_from_runtime_result(
    runtime_result: OcrRuntimeResult,
) -> UserStatusObservation:
    return build_user_status_observation(
        present=runtime_result.present,
        ocr_text=runtime_result.text,
        metadata=runtime_result.metadata,
        artifact_path=str(runtime_result.screenshot_path),
    )


def _sense_result_payload(observation: UserStatusObservation) -> dict[str, Any]:
    return {
        "present": observation.present,
        "activity_label": observation.activity_label,
        "summary": observation.summary,
        "confidence": observation.confidence,
        "visible_text": observation.visible_text[:400],
        "app_name": observation.app_name,
        "window_title": observation.window_title,
        "url": observation.url,
        "artifact_path": observation.artifact_path,
    }


async def _insert_observation(conn: Any, observation: UserStatusObservation) -> None:
    command = insert_user_status_observation_sql(observation)
    await conn.execute(command.query, command.params)
