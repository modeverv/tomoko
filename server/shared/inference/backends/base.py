from __future__ import annotations

import abc
from collections.abc import AsyncGenerator


class InferenceBackend(abc.ABC):
    name: str
    privacy_allowed: bool

    @abc.abstractmethod
    async def chat_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        *,
        trace_role: str | None = None,
    ) -> AsyncGenerator[str, None]:
        del trace_role
        if False:
            yield ""
