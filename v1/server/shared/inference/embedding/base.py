from __future__ import annotations

import abc


class EmbeddingBackend(abc.ABC):
    name: str
    model: str
    dimensions: int
    privacy_allowed: bool = True

    @abc.abstractmethod
    async def embed_query(self, text: str) -> list[float]: ...

    @abc.abstractmethod
    async def embed_passage(self, text: str) -> list[float]: ...

    async def warm_up(self) -> None:
        await self.embed_query("トモコ、覚えてる？")
