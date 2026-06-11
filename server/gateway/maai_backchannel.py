from __future__ import annotations

import asyncio
import inspect
import logging
import os
import queue
import wave
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

import numpy as np

from server.shared.models import BackchannelSuggestion

logger = logging.getLogger(__name__)

MAAI_SAMPLE_RATE = 16000
MAAI_FRAME_SIZE = 160


@dataclass(frozen=True)
class MaaiBackchannelConfig:
    lang: str = "jp"
    frame_rate: int | float = 10
    context_len_sec: int = 5
    device: str = "cpu"
    react_threshold: float = 0.50
    emo_threshold: float = 0.35
    cooldown_ms: int = 900
    vap_hybrid_enabled: bool = False
    min_silence_ms: int = 150
    delta_silence_ms: int = 650
    threshold_probability: float = 0.90


class MaaiBackchannelTap:
    """AudioInteractionTap implementation backed by MaAI bc_2type and VAP hybrid."""

    def __init__(
        self,
        *,
        config: MaaiBackchannelConfig,
        suggestion_callback: Callable[[BackchannelSuggestion], Any] | None = None,
        maai_module: Any | None = None,
    ) -> None:
        self.config = config
        self._suggestion_callback = suggestion_callback
        self._maai_module = maai_module
        self._audio_ch1: Any | None = None
        self._audio_ch2: Any | None = None
        self._maai: Any | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._running = False
        self._last_suggestion_at: datetime | None = None

        # VAP Hybrid fields
        self._maai_vap: Any | None = None
        self._poll_vap_task: asyncio.Task[None] | None = None
        self._audio_ch1_vap: Any | None = None
        self._audio_ch2_vap: Any | None = None
        self._recommended_silence_ms: int | None = None

    def set_suggestion_callback(
        self,
        callback: Callable[[BackchannelSuggestion], Any] | None,
    ) -> None:
        self._suggestion_callback = callback

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

        if self.config.vap_hybrid_enabled:
            self._audio_ch1_vap = maai_module.MaaiInput.Chunk()
            self._audio_ch2_vap = maai_module.MaaiInput.Chunk()
            self._maai_vap = maai_module.Maai(
                mode="vap",
                lang=self.config.lang,
                frame_rate=self.config.frame_rate,
                context_len_sec=self.config.context_len_sec,
                audio_ch1=self._audio_ch1_vap,
                audio_ch2=self._audio_ch2_vap,
                device=self.config.device,
            )
            self._maai_vap.start()
            self._poll_vap_task = asyncio.create_task(self._poll_vap_results())
            logger.info("Maai VAP prediction model started.")

        self._running = True
        self._poll_task = asyncio.create_task(self._poll_results())
        logger.info(
            "MaaiBackchannelTap started lang=%s frame_rate=%s "
            "context_len_sec=%s device=%s vap_hybrid_enabled=%s",
            self.config.lang,
            self.config.frame_rate,
            self.config.context_len_sec,
            self.config.device,
            self.config.vap_hybrid_enabled,
        )

    async def stop(self) -> None:
        self._running = False
        if self._poll_task is not None:
            self._poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._poll_task
            self._poll_task = None

        if self._poll_vap_task is not None:
            self._poll_vap_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._poll_vap_task
            self._poll_vap_task = None

        if self._maai is not None:
            stop = getattr(self._maai, "stop", None)
            if stop is not None:
                stop(wait=False)
        self._maai = None

        if self._maai_vap is not None:
            stop = getattr(self._maai_vap, "stop", None)
            if stop is not None:
                stop(wait=False)
        self._maai_vap = None

    def observe_user_audio(self, chunk: np.ndarray, *, observed_at: datetime) -> None:
        del observed_at
        self._feed_two_channel(user_audio=np.asarray(chunk, dtype=np.float32))

    def observe_tomoko_audio(self, chunk: bytes, *, observed_at: datetime) -> None:
        del observed_at
        audio = wav_bytes_to_float32_mono_16k(chunk)
        if audio.size == 0:
            return
        self._feed_two_channel(tomoko_audio=audio)

    def observe_duplex_audio(
        self,
        *,
        user_chunk: np.ndarray,
        tomoko_chunk: np.ndarray,
        observed_at: datetime,
    ) -> None:
        del observed_at
        self._feed_two_channel(
            user_audio=np.asarray(user_chunk, dtype=np.float32),
            tomoko_audio=np.asarray(tomoko_chunk, dtype=np.float32),
        )

    def handle_result(
        self,
        result: dict[str, Any],
        *,
        observed_at: datetime | None = None,
    ) -> BackchannelSuggestion | None:
        observed = observed_at or datetime.now(UTC)
        suggestion = self._suggestion_from_result(result, observed_at=observed)
        if suggestion is None:
            return None
        self._last_suggestion_at = observed
        self._emit_suggestion(suggestion)
        return suggestion

    async def _poll_results(self) -> None:
        assert self._maai is not None
        while self._running:
            try:
                result = await asyncio.to_thread(_read_maai_result_once, self._maai)
            except asyncio.CancelledError:
                raise
            except queue.Empty:
                continue
            except Exception:
                logger.warning("MaaiBackchannelTap result polling failed", exc_info=True)
                await asyncio.sleep(0.2)
                continue
            if isinstance(result, dict):
                self.handle_result(result)

    async def _poll_vap_results(self) -> None:
        assert self._maai_vap is not None
        while self._running:
            try:
                result = await asyncio.to_thread(_read_maai_result_once, self._maai_vap)
            except asyncio.CancelledError:
                raise
            except queue.Empty:
                continue
            except Exception:
                logger.warning("Maai VAP result polling failed", exc_info=True)
                await asyncio.sleep(0.2)
                continue
            if isinstance(result, dict):
                self.handle_vap_result(result)

    def handle_vap_result(self, result: dict[str, Any]) -> None:
        p_future = result.get("p_future")
        if not isinstance(p_future, list) or len(p_future) < 2:
            return
        p_yielding = _float_or_zero(p_future[1])

        threshold = self.config.threshold_probability
        if p_yielding >= threshold:
            # 確度が高いほど待ち時間を縮める（逆数的）
            silence_ms = (
                self.config.min_silence_ms
                + (1.0 - p_yielding) * self.config.delta_silence_ms
            )
            self._recommended_silence_ms = int(round(silence_ms))
        else:
            # しきい値未満は最大待ち時間
            self._recommended_silence_ms = int(
                self.config.min_silence_ms + self.config.delta_silence_ms
            )

    def get_recommended_silence_ms(self) -> int | None:
        return self._recommended_silence_ms

    def _feed_two_channel(
        self,
        *,
        user_audio: np.ndarray | None = None,
        tomoko_audio: np.ndarray | None = None,
    ) -> None:
        if self._audio_ch1 is None or self._audio_ch2 is None:
            return
        if user_audio is None and tomoko_audio is None:
            return
        length = max(
            int(user_audio.size) if user_audio is not None else 0,
            int(tomoko_audio.size) if tomoko_audio is not None else 0,
        )
        if length <= 0:
            return
        user = _pad_or_trim(user_audio, length)
        tomoko = _pad_or_trim(tomoko_audio, length)
        for start in range(0, length - MAAI_FRAME_SIZE + 1, MAAI_FRAME_SIZE):
            chunk1 = user[start : start + MAAI_FRAME_SIZE]
            chunk2 = tomoko[start : start + MAAI_FRAME_SIZE]
            self._audio_ch1.put_chunk(chunk1)
            self._audio_ch2.put_chunk(chunk2)
            if self._audio_ch1_vap is not None and self._audio_ch2_vap is not None:
                self._audio_ch1_vap.put_chunk(chunk1)
                self._audio_ch2_vap.put_chunk(chunk2)

    def _suggestion_from_result(
        self,
        result: dict[str, Any],
        *,
        observed_at: datetime,
    ) -> BackchannelSuggestion | None:
        react_score = _float_or_zero(result.get("p_bc_react"))
        emo_score = _float_or_zero(result.get("p_bc_emo"))
        if react_score < self.config.react_threshold and emo_score < self.config.emo_threshold:
            return None
        if self._last_suggestion_at is not None:
            elapsed_ms = (observed_at - self._last_suggestion_at).total_seconds() * 1000
            if elapsed_ms < self.config.cooldown_ms:
                return None
        if emo_score >= self.config.emo_threshold and emo_score > react_score:
            return BackchannelSuggestion(
                kind="emo",
                score=emo_score,
                source="maai",
                observed_at=observed_at,
                reason="p_bc_emo_threshold",
            )
        return BackchannelSuggestion(
            kind="react",
            score=react_score,
            source="maai",
            observed_at=observed_at,
            reason="p_bc_react_threshold",
        )

    def _emit_suggestion(self, suggestion: BackchannelSuggestion) -> None:
        if self._suggestion_callback is None:
            return
        try:
            result = self._suggestion_callback(suggestion)
            if inspect.isawaitable(result):
                asyncio.create_task(result)
        except Exception:
            logger.warning("MaaiBackchannelTap suggestion callback failed", exc_info=True)


def create_maai_backchannel_tap_from_env(
    *,
    suggestion_callback: Callable[[BackchannelSuggestion], Any] | None = None,
    maai_module: Any | None = None,
    config_audio: Any | None = None,
) -> MaaiBackchannelTap | None:
    enabled_env = os.environ.get("TOMOKO_MAAI_BACKCHANNEL_ENABLED", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    enabled_config = config_audio is not None and getattr(config_audio, "vap_hybrid_enabled", False)

    if not (enabled_env or enabled_config):
        return None

    react_threshold = _env_float("TOMOKO_MAAI_REACT_THRESHOLD", 0.50)
    emo_threshold = _env_float("TOMOKO_MAAI_EMO_THRESHOLD", 0.35)
    cooldown_ms = _env_int("TOMOKO_MAAI_COOLDOWN_MS", 900)
    device = os.environ.get("TOMOKO_MAAI_DEVICE", "cpu")
    frame_rate = _env_number("TOMOKO_MAAI_FRAME_RATE", 10)
    context_len_sec = _env_int("TOMOKO_MAAI_CONTEXT_LEN_SEC", 5)

    vap_hybrid_enabled = enabled_config or _env_bool(
        "TOMOKO_MAAI_VAP_HYBRID_ENABLED", False
    )

    min_silence_ms = 150
    delta_silence_ms = 650
    threshold_probability = 0.90
    if config_audio is not None:
        min_silence_ms = getattr(
            config_audio, "vap_hybrid_min_silence_ms", min_silence_ms
        )
        if hasattr(config_audio, "vap_hybrid_delta_silence_ms"):
            delta_silence_ms = config_audio.vap_hybrid_delta_silence_ms
        else:
            max_silence_ms = getattr(
                config_audio,
                "vap_hybrid_max_silence_ms",
                min_silence_ms + delta_silence_ms,
            )
            delta_silence_ms = max_silence_ms - min_silence_ms
        threshold_probability = getattr(
            config_audio,
            "vap_hybrid_threshold_probability",
            threshold_probability,
        )

    # env overrides
    min_silence_ms = _env_int(
        "TOMOKO_MAAI_VAP_HYBRID_MIN_SILENCE_MS", min_silence_ms
    )
    if "TOMOKO_MAAI_VAP_HYBRID_DELTA_SILENCE_MS" in os.environ:
        delta_silence_ms = _env_int(
            "TOMOKO_MAAI_VAP_HYBRID_DELTA_SILENCE_MS", delta_silence_ms
        )
    elif "TOMOKO_MAAI_VAP_HYBRID_MAX_SILENCE_MS" in os.environ:
        max_silence_ms = _env_int(
            "TOMOKO_MAAI_VAP_HYBRID_MAX_SILENCE_MS",
            min_silence_ms + delta_silence_ms,
        )
        delta_silence_ms = max_silence_ms - min_silence_ms
    threshold_probability = _env_float(
        "TOMOKO_MAAI_VAP_HYBRID_THRESHOLD_PROBABILITY", threshold_probability
    )

    return MaaiBackchannelTap(
        config=MaaiBackchannelConfig(
            react_threshold=react_threshold,
            emo_threshold=emo_threshold,
            cooldown_ms=cooldown_ms,
            device=device,
            frame_rate=frame_rate,
            context_len_sec=context_len_sec,
            vap_hybrid_enabled=vap_hybrid_enabled,
            min_silence_ms=min_silence_ms,
            delta_silence_ms=delta_silence_ms,
            threshold_probability=threshold_probability,
        ),
        suggestion_callback=suggestion_callback,
        maai_module=maai_module,
    )


def wav_bytes_to_float32_mono_16k(data: bytes) -> np.ndarray:
    try:
        with wave.open(BytesIO(data), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            frame_count = wav.getnframes()
            raw = wav.readframes(frame_count)
    except wave.Error:
        return np.empty(0, dtype=np.float32)

    if sample_width == 2:
        audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sample_width == 4:
        audio = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        return np.empty(0, dtype=np.float32)
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if sample_rate != MAAI_SAMPLE_RATE:
        audio = _resample_linear(audio, sample_rate, MAAI_SAMPLE_RATE)
    return np.asarray(audio, dtype=np.float32)


def _import_maai() -> Any:
    try:
        import maai  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "MaAI is not installed. Install it separately, then set "
            "TOMOKO_MAAI_BACKCHANNEL_ENABLED=1."
        ) from exc
    return maai


def _read_maai_result_once(maai_obj: Any) -> Any:
    result_queue = getattr(maai_obj, "result_dict_queue", None)
    get = getattr(result_queue, "get", None)
    if get is not None:
        return get(timeout=0.2)
    return maai_obj.get_result()


def _pad_or_trim(audio: np.ndarray | None, length: int) -> np.ndarray:
    if audio is None or audio.size == 0:
        return np.zeros(length, dtype=np.float32)
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size >= length:
        return audio[:length]
    padded = np.zeros(length, dtype=np.float32)
    padded[: audio.size] = audio
    return padded


def _resample_linear(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if audio.size == 0 or source_rate <= 0 or target_rate <= 0:
        return np.empty(0, dtype=np.float32)
    if source_rate == target_rate:
        return np.asarray(audio, dtype=np.float32)
    duration = audio.size / source_rate
    target_size = max(1, int(round(duration * target_rate)))
    source_x = np.linspace(0.0, duration, num=audio.size, endpoint=False)
    target_x = np.linspace(0.0, duration, num=target_size, endpoint=False)
    return np.interp(target_x, source_x, audio).astype(np.float32)


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _env_float(name: str, default: float) -> float:
    return _float_or_zero(os.environ.get(name, default))


def _env_number(name: str, default: int | float) -> int | float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    if parsed.is_integer():
        return int(parsed)
    return parsed


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.lower() in {"1", "true", "yes", "on"}
