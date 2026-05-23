import abc
from collections.abc import AsyncGenerator
from server.shared.inference.backends.base import InferenceBackend

class ThinkingMode(abc.ABC):
    @abc.abstractmethod
    async def think(
        self, backend: InferenceBackend, transcript: str
    ) -> AsyncGenerator[str, None]:
        pass
        yield "" # type checking
