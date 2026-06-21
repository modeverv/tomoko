from __future__ import annotations

import asyncio
import json
import math
import os
from dataclasses import dataclass
from typing import Any

import websockets

from server.shared.models import TurnMaterials


@dataclass(slots=True)
class TurnMaterialAggregator:
    window_ms: int = 200
    speech_rms_threshold: float = 0.02
    _last_emit_ms: float | None = None
    _last_audio_rms: float = 0.0
    _silence_ms: int = 0
    _p_yielding: float | None = None
    _p_bc_react: float | None = None
    _p_bc_emo: float | None = None

    def observe_audio(
        self,
        samples: tuple[float, ...],
        *,
        now_ms: float,
        playback_active: bool = False,
        stt_partial: str = "",
    ) -> TurnMaterials | None:
        rms = _rms(samples)
        self._last_audio_rms = rms
        speech_probability = _clamp(rms / self.speech_rms_threshold)
        if speech_probability >= 0.2:
            self._silence_ms = 0
        else:
            self._silence_ms += self.window_ms
        if self._last_emit_ms is None:
            self._last_emit_ms = now_ms
            return None
        if now_ms - self._last_emit_ms < self.window_ms:
            return None
        self._last_emit_ms = now_ms
        materials = self.snapshot(
            now_ms=now_ms,
            playback_active=playback_active,
            stt_partial=stt_partial,
        )
        _console_event(
            "snapshot",
            p_yielding=materials.p_yielding,
            p_bc_react=materials.p_bc_react,
            p_bc_emo=materials.p_bc_emo,
            speech_probability=round(materials.speech_probability, 4),
            silence_ms=materials.silence_ms,
            playback_active=materials.playback_active,
        )
        return materials

    def observe_maai_result(self, result: dict[str, Any]) -> None:
        self._p_bc_react = _optional_float(result.get("p_bc_react"))
        self._p_bc_emo = _optional_float(result.get("p_bc_emo"))
        self._p_yielding = _optional_float(
            result.get("p_yielding")
            if "p_yielding" in result
            else result.get("p_turn_yielding")
        )
        _console_event(
            "maai_result",
            raw_keys=sorted(str(key) for key in result),
            p_yielding=self._p_yielding,
            p_bc_react=self._p_bc_react,
            p_bc_emo=self._p_bc_emo,
        )

    def snapshot(
        self,
        *,
        now_ms: float,
        playback_active: bool = False,
        stt_partial: str = "",
    ) -> TurnMaterials:
        del now_ms
        speech_probability = _clamp(self._last_audio_rms / self.speech_rms_threshold)
        return TurnMaterials(
            window_ms=self.window_ms,
            user_speaking=speech_probability >= 0.2,
            speech_probability=speech_probability,
            p_yielding=self._p_yielding,
            silence_ms=self._silence_ms,
            playback_active=playback_active,
            p_bc_react=self._p_bc_react,
            p_bc_emo=self._p_bc_emo,
            audio_rms=self._last_audio_rms,
            stt_partial=stt_partial,
        )


class InternalTurnMaterialClient:
    def __init__(self, url: str | None = None, *, max_queue_size: int = 32) -> None:
        self.url = url if url is not None else os.environ.get("TOMOKO_INTERNAL_WS_URL", "")
        self._queue: asyncio.Queue[TurnMaterials] = asyncio.Queue(maxsize=max_queue_size)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if not self.url or self._task is not None:
            if not self.url:
                _console_event("ws_client_disabled", reason="missing_url")
            return
        _console_event("ws_client_start", url=self.url)
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def submit(self, materials: TurnMaterials) -> None:
        if not self.url:
            _console_event("submit_skipped", reason="missing_url")
            return
        try:
            self._queue.put_nowait(materials)
            _console_event(
                "submit",
                queue_size=self._queue.qsize(),
                p_yielding=materials.p_yielding,
                speech_probability=round(materials.speech_probability, 4),
                silence_ms=materials.silence_ms,
            )
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                pass
            self._queue.put_nowait(materials)
            _console_event(
                "submit_dropped_oldest",
                queue_size=self._queue.qsize(),
                p_yielding=materials.p_yielding,
            )

    async def _run(self) -> None:
        while True:
            try:
                _console_event("ws_connecting", url=self.url)
                async with websockets.connect(self.url) as websocket:
                    ready = await websocket.recv()
                    _console_event("ws_connected", ready=ready)
                    while True:
                        materials = await self._queue.get()
                        try:
                            payload = {"type": "turn_materials", **materials.to_dict()}
                            _console_event(
                                "ws_send",
                                materials_id=materials.id,
                                p_yielding=materials.p_yielding,
                                speech_probability=round(materials.speech_probability, 4),
                                silence_ms=materials.silence_ms,
                            )
                            await websocket.send(json.dumps(payload, ensure_ascii=False))
                            with _ignore_ack_errors():
                                ack = await asyncio.wait_for(websocket.recv(), timeout=0.05)
                                _console_event("ws_ack", ack=ack)
                        finally:
                            self._queue.task_done()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _console_event("turn_materials_ws_disconnected", error=type(exc).__name__)
                await asyncio.sleep(1.0)


class _ignore_ack_errors:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_args: object) -> bool:
        return True


def _rms(samples: tuple[float, ...]) -> float:
    if not samples:
        return 0.0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return _clamp(float(value))
    except (TypeError, ValueError):
        return None


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _console_event(event: str, **fields: object) -> None:
    parts = [f"[tomoko:turn-materials] {event}"]
    for key, value in fields.items():
        parts.append(f"{key}={str(value)!r}")
    print(" ".join(parts), flush=True)
