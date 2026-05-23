from __future__ import annotations

import abc
from collections.abc import AsyncGenerator

from server.shared.models import AudioChunkOut, TTSInput


class TTSBackend(abc.ABC):
    name: str

    @abc.abstractmethod
    async def synthesize(self, tts_input: TTSInput) -> AsyncGenerator[AudioChunkOut, None]:
        if False:
            yield AudioChunkOut(data=b"", sequence=0, is_last=True)
