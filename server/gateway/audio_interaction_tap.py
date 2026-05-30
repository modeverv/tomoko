from __future__ import annotations

from collections.abc import Awaitable
from datetime import datetime
from typing import Any, Protocol

import numpy as np


class AudioInteractionTap(Protocol):
    """Optional observer for continuous interaction-model audio sidecars."""

    def observe_user_audio(
        self,
        chunk: np.ndarray,
        *,
        observed_at: datetime,
    ) -> Awaitable[None] | None:
        """Observe user mic audio without owning the hot path."""
        ...

    def observe_tomoko_audio(
        self,
        chunk: bytes,
        *,
        observed_at: datetime,
    ) -> Awaitable[None] | None:
        """Observe Tomoko output audio without owning browser send timing."""
        ...


def maybe_schedule_tap_result(result: Any) -> None:
    if not isinstance(result, Awaitable):
        return
    import asyncio

    task = asyncio.create_task(result)
    task.add_done_callback(_log_tap_task_failure)


def _log_tap_task_failure(task: Awaitable[None]) -> None:
    try:
        task.result()  # type: ignore[attr-defined]
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "AudioInteractionTap async observer failed",
            exc_info=True,
        )
