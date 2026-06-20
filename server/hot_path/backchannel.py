from __future__ import annotations

import asyncio
import inspect
import os
import queue
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from server.shared.models import AudioChunkOut, new_id

MAAI_SAMPLE_RATE = 16000
MAAI_FRAME_SIZE = 160
BACKCHANNEL_ASSETS = (
    ("うん", "un.wav"),
    ("へえ", "hee.wav"),
    ("ほう", "hou.wav"),
)


@dataclass(frozen=True, slots=True)
class MaaiBackchannelConfig:
    threshold: float = 0.50
    cooldown_ms: int = 1500
    lang: str = "jp"
    frame_rate: int | float = 10
    context_len_sec: int = 5
    device: str = "cpu"


@dataclass(frozen=True, slots=True)
class BackchannelAudio:
    text: str
    audio: bytes


@dataclass(frozen=True, slots=True)
class BackchannelEmission:
    text: str
    audio_chunk: AudioChunkOut
    score: float
    reason: str


@dataclass(slots=True)
class BackchannelAssetStore:
    asset_dir: Path
    _index: int = 0
    _assets: tuple[BackchannelAudio, ...] = field(init=False)

    def __post_init__(self) -> None:
        assets: list[BackchannelAudio] = []
        for text, filename in BACKCHANNEL_ASSETS:
            path = self.asset_dir / filename
            audio = path.read_bytes()
            if not _is_complete_wav(audio):
                raise ValueError(f"backchannel asset must be complete WAV: {path}")
            assets.append(BackchannelAudio(text=text, audio=audio))
        self._assets = tuple(assets)

    def next_chunk(self) -> BackchannelAudio:
        if not self._assets:
            raise RuntimeError("no backchannel assets loaded")
        item = self._assets[self._index % len(self._assets)]
        self._index += 1
        return item


class MaaiBackchannelDetector:
    def __init__(
        self,
        *,
        config: MaaiBackchannelConfig,
        assets: BackchannelAssetStore,
        playback_active: Callable[[], bool] | None = None,
        emission_callback: Callable[[BackchannelEmission], Any] | None = None,
        result_callback: Callable[[dict[str, Any]], Any] | None = None,
        maai_module: Any | None = None,
    ) -> None:
        self.config = config
        self.assets = assets
        self.playback_active = playback_active or (lambda: False)
        self.emission_callback = emission_callback
        self.result_callback = result_callback
        self._maai_module = maai_module
        self._audio_ch1: Any | None = None
        self._audio_ch2: Any | None = None
        self._maai: Any | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._running = False
        self._last_emitted_at: datetime | None = None
        self._poll_error_counts: dict[str, int] = {}

    async def start(self) -> None:
        if self._running:
            return
        maai_module = self._maai_module or _import_maai()
        self._audio_ch1 = maai_module.MaaiInput.Chunk()
        self._audio_ch2 = maai_module.MaaiInput.Chunk()
        self._maai = maai_module.Maai(
            mode="bc_2type",
            lang=self.config.lang,
            frame_rate=self.config.frame_rate,
            context_len_sec=self.config.context_len_sec,
            audio_ch1=self._audio_ch1,
            audio_ch2=self._audio_ch2,
            device=self.config.device,
        )
        self._maai.start()
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_results())

    async def stop(self) -> None:
        self._running = False
        if self._poll_task is not None:
            self._poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._poll_task
            self._poll_task = None
        if self._maai is not None:
            stop = getattr(self._maai, "stop", None)
            if stop is not None:
                stop(wait=False)
        self._maai = None

    def observe_user_audio(self, samples: tuple[float, ...]) -> None:
        if self._audio_ch1 is None or self._audio_ch2 is None or not samples:
            return
        user = np.asarray(samples, dtype=np.float32)
        silence = np.zeros(user.size, dtype=np.float32)
        for start in range(0, user.size - MAAI_FRAME_SIZE + 1, MAAI_FRAME_SIZE):
            self._audio_ch1.put_chunk(user[start : start + MAAI_FRAME_SIZE])
            self._audio_ch2.put_chunk(silence[start : start + MAAI_FRAME_SIZE])

    def handle_result(
        self,
        result: dict[str, Any],
        *,
        observed_at: datetime | None = None,
    ) -> BackchannelEmission | None:
        if self.playback_active():
            return None
        observed = observed_at or datetime.now(UTC)
        react_score = _float_or_zero(result.get("p_bc_react"))
        emo_score = _float_or_zero(result.get("p_bc_emo"))
        score = max(react_score, emo_score)
        if score < self.config.threshold:
            return None
        if self._last_emitted_at is not None:
            elapsed_ms = (observed - self._last_emitted_at).total_seconds() * 1000.0
            if elapsed_ms < self.config.cooldown_ms:
                return None
        asset = self.assets.next_chunk()
        request_id = new_id()
        emission = BackchannelEmission(
            text=asset.text,
            audio_chunk=AudioChunkOut(
                request_id=request_id,
                chunk=asset.audio,
                sample_rate=MAAI_SAMPLE_RATE,
                is_final=True,
                trace_id=request_id,
            ),
            score=score,
            reason=(
                "p_bc_emo_threshold"
                if emo_score >= self.config.threshold and emo_score > react_score
                else "p_bc_react_threshold"
            ),
        )
        self._last_emitted_at = observed
        self._emit(emission)
        return emission

    async def _poll_results(self) -> None:
        assert self._maai is not None
        while self._running:
            try:
                result = await asyncio.to_thread(_read_maai_result_once, self._maai)
            except asyncio.CancelledError:
                raise
            except queue.Empty:
                continue
            except Exception as exc:
                error_name = type(exc).__name__
                self._poll_error_counts[error_name] = (
                    self._poll_error_counts.get(error_name, 0) + 1
                )
                if _should_log_poll_error(self._poll_error_counts[error_name]):
                    _console_event(
                        "maai_backchannel_poll_error",
                        error=error_name,
                        count=self._poll_error_counts[error_name],
                    )
                await asyncio.sleep(0.2)
                continue
            if isinstance(result, dict):
                self._emit_result(result)
                self.handle_result(result)

    def _emit(self, emission: BackchannelEmission) -> None:
        if self.emission_callback is None:
            return
        result = self.emission_callback(emission)
        if inspect.isawaitable(result):
            asyncio.create_task(_await_callback(result))

    def _emit_result(self, result: dict[str, Any]) -> None:
        if self.result_callback is None:
            return
        callback_result = self.result_callback(result)
        if inspect.isawaitable(callback_result):
            asyncio.create_task(_await_callback(callback_result))


def create_backchannel_detector_from_env(
    *,
    asset_dir: Path | None = None,
    playback_active: Callable[[], bool] | None = None,
    emission_callback: Callable[[BackchannelEmission], Any] | None = None,
    result_callback: Callable[[dict[str, Any]], Any] | None = None,
    maai_module: Any | None = None,
) -> MaaiBackchannelDetector | None:
    if os.environ.get("TOMOKO_V2_MAAI_BACKCHANNEL", "0") != "1":
        return None
    assets_path = asset_dir or Path(
        os.environ.get("TOMOKO_V2_BACKCHANNEL_ASSET_DIR", "assets/backchannels")
    )
    try:
        assets = BackchannelAssetStore(assets_path)
    except Exception as exc:
        _console_event(
            "maai_backchannel_disabled",
            reason="asset_load_failed",
            error=type(exc).__name__,
        )
        return None
    return MaaiBackchannelDetector(
        config=MaaiBackchannelConfig(
            threshold=_env_float("TOMOKO_V2_MAAI_BACKCHANNEL_THRESHOLD", 0.50),
            cooldown_ms=_env_int("TOMOKO_V2_MAAI_BACKCHANNEL_COOLDOWN_MS", 1500),
            device=os.environ.get("TOMOKO_V2_MAAI_DEVICE", "cpu"),
            frame_rate=_env_number("TOMOKO_V2_MAAI_FRAME_RATE", 10),
            context_len_sec=_env_int("TOMOKO_V2_MAAI_CONTEXT_LEN_SEC", 5),
        ),
        assets=assets,
        playback_active=playback_active,
        emission_callback=emission_callback,
        result_callback=result_callback,
        maai_module=maai_module,
    )


async def _await_callback(awaitable: Awaitable[Any]) -> None:
    try:
        await awaitable
    except Exception as exc:
        _console_event("maai_backchannel_callback_error", error=type(exc).__name__)


def _read_maai_result_once(maai_instance: Any) -> Any:
    if hasattr(maai_instance, "result_dict_queue"):
        result = _queue_get_once(maai_instance.result_dict_queue)
    elif hasattr(maai_instance, "output_queue"):
        result = _queue_get_once(maai_instance.output_queue)
    else:
        result = maai_instance.get_result()
    return result[0] if isinstance(result, tuple) and result else result


def _queue_get_once(result_queue: Any) -> Any:
    try:
        return result_queue.get(timeout=0.1)
    except TypeError as exc:
        empty = getattr(result_queue, "empty", None)
        if callable(empty) and empty():
            raise queue.Empty from exc
        return result_queue.get()


def _should_log_poll_error(count: int) -> bool:
    return count == 1 or count in {10, 100} or count % 1000 == 0


def _import_maai() -> Any:
    try:
        import maai  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("MaAI is not installed") from exc
    return maai


def _is_complete_wav(chunk: bytes) -> bool:
    return len(chunk) >= 12 and chunk[:4] == b"RIFF" and chunk[8:12] == b"WAVE"


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_number(name: str, default: int | float) -> int | float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        number = float(value)
    except ValueError:
        return default
    return int(number) if number.is_integer() else number


def _console_event(event: str, **fields: object) -> None:
    parts = [f"[tomoko:backchannel] {event}"]
    for key, value in fields.items():
        parts.append(f"{key}={str(value)!r}")
    print(" ".join(parts), flush=True)
