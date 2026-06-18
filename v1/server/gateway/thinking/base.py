from __future__ import annotations

import abc
from collections.abc import AsyncGenerator

from server.shared.inference.backends.base import InferenceBackend
from server.shared.models import ThinkingEvent, ThinkingInput


class ThinkingMode(abc.ABC):
    @abc.abstractmethod
    async def think(
        self, backend: InferenceBackend, thinking_input: ThinkingInput
    ) -> AsyncGenerator[ThinkingEvent, None]:
        if False:
            yield ThinkingEvent(type="done", value="")
