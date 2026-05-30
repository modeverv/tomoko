from __future__ import annotations

import inspect
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from server.shared.inference.tts.base import TTSBackend
from server.shared.models import BackchannelSuggestion, TomoroRuntimeState, TTSInput

logger = logging.getLogger(__name__)

GESTURE_BACKCHANNEL_REACT_THRESHOLD = 0.45
GESTURE_BACKCHANNEL_COOLDOWN_MS = 1500
GESTURE_BACKCHANNEL_REACT_UTTERANCES = ("うん", "なるほど", "そっか")


@dataclass(frozen=True)
class GestureBackchannelResult:
    released: bool
    reason: str | None
    text: str | None


class GestureAudioEmitter:
    """Emits non-turn gesture audio without mutating TomoroSession state."""

    def __init__(
        self,
        *,
        state_provider: Callable[[], TomoroRuntimeState],
        send_audio,
        send_event,
        tts_backend: TTSBackend | None,
        audio_observer: Any | None = None,
        react_threshold: float = GESTURE_BACKCHANNEL_REACT_THRESHOLD,
        cooldown_ms: int = GESTURE_BACKCHANNEL_COOLDOWN_MS,
        react_utterances: tuple[str, ...] = GESTURE_BACKCHANNEL_REACT_UTTERANCES,
    ) -> None:
        self._state_provider = state_provider
        self._send_audio = send_audio
        self._send_event = send_event
        self._tts_backend = tts_backend
        self._audio_observer = audio_observer
        self._react_threshold = react_threshold
        self._cooldown_ms = cooldown_ms
        self._react_utterances = react_utterances
        self._last_released_at: datetime | None = None

    async def release_backchannel(
        self,
        suggestion: BackchannelSuggestion,
    ) -> GestureBackchannelResult:
        state = self._state_provider()
        skip_reason = self._skip_reason(suggestion, state)
        payload = {
            **suggestion.to_json(),
            "threshold": self._react_threshold,
            "cooldown_ms": self._cooldown_ms,
            "vad_state": state.vad_state,
            "playback_state": state.playback_state,
            "lane": "gesture_audio",
        }
        if skip_reason is not None:
            await self._emit_event(
                "backchannel_skipped",
                {**payload, "reason": skip_reason},
            )
            return GestureBackchannelResult(
                released=False,
                reason=skip_reason,
                text=None,
            )

        text = random.choice(self._react_utterances)
        await self._speak(text)
        self._last_released_at = suggestion.observed_at
        await self._emit_event(
            "backchannel_released",
            {**payload, "text": text},
        )
        await self._send_json({"type": "reply_done", "control": "backchannel"})
        return GestureBackchannelResult(released=True, reason=None, text=text)

    def _skip_reason(
        self,
        suggestion: BackchannelSuggestion,
        state: TomoroRuntimeState,
    ) -> str | None:
        if suggestion.kind != "react":
            return "unsupported_kind"
        if suggestion.score < self._react_threshold:
            return "below_threshold"
        if state.attention_mode == "ambient":
            return "attention_not_engaged"
        if state.vad_state != "listening":
            return "user_not_speaking"
        if state.playback_state != "idle":
            return "tomoko_not_idle"
        if self._last_released_at is not None:
            elapsed_ms = (
                suggestion.observed_at - self._last_released_at
            ).total_seconds() * 1000
            if elapsed_ms < self._cooldown_ms:
                return "cooldown_active"
        if self._tts_backend is None or self._send_audio is None:
            return "audio_output_unavailable"
        if not self._react_utterances:
            return "audio_output_unavailable"
        return None

    async def _speak(self, text: str) -> None:
        assert self._tts_backend is not None
        tts_input = TTSInput(text=text, style="gentle")
        async for chunk in self._tts_backend.synthesize(tts_input):
            self._observe_tomoko_audio(chunk.data)
            maybe_awaitable = self._send_audio(chunk.data)
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable

    def _observe_tomoko_audio(self, chunk: bytes) -> None:
        observer = getattr(self._audio_observer, "observe_tomoko_audio", None)
        if observer is None:
            return
        try:
            result = observer(chunk, observed_at=datetime.now().astimezone())
            if inspect.isawaitable(result):
                logger.debug("ignored async gesture audio observer result")
        except Exception:
            logger.warning("GestureAudioEmitter tomoko observer failed", exc_info=True)

    async def _emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        await self._send_json({"type": event_type, **payload})

    async def _send_json(self, event: dict[str, Any]) -> None:
        if self._send_event is None:
            return
        maybe_awaitable = self._send_event(event)
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable
