from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import Any, Literal

import numpy as np

from server.edge.pipeline.vad import VADProcessor
from server.shared.models import SpeechSegment

SessionState = Literal["idle", "listening", "processing"]

logger = logging.getLogger(__name__)


class TomoroSession:
    def __init__(
        self,
        *,
        vad_processor: VADProcessor,
        send_event: Callable[[dict[str, str]], Any],
    ) -> None:
        self.vad_processor = vad_processor
        self.send_event = send_event
        self.state: SessionState = "idle"
        self.latest_segment: SpeechSegment | None = None

    async def process_audio_chunk(self, chunk_bytes: bytes) -> SpeechSegment | None:
        chunk = np.frombuffer(chunk_bytes, dtype=np.float32)
        result = self.vad_processor.process_chunk(chunk)
        if result.state_changed_to is not None:
            await self._transition(result.state_changed_to)
        if result.segment is not None:
            self.latest_segment = result.segment
        return result.segment

    async def _transition(self, state: str) -> None:
        if state not in {"idle", "listening", "processing"}:
            raise ValueError(f"unknown session state: {state}")
        self.state = state  # type: ignore[assignment]
        logger.info("TomoroSession state changed to %s", state)
        event = {"type": "state", "state": state}
        maybe_awaitable = self.send_event(event)
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable
