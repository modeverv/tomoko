import abc
from collections.abc import AsyncGenerator


class InferenceBackend(abc.ABC):
    name: str
    privacy_allowed: bool

    @abc.abstractmethod
    async def chat_stream(
        self, system_prompt: str, messages: list[dict[str, str]]
    ) -> AsyncGenerator[str, None]:
        pass
        yield "" # to make it a generator for type checking if needed
