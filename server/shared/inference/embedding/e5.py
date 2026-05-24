from __future__ import annotations

import asyncio
from typing import Any

import numpy as np

from server.shared.inference.embedding.base import EmbeddingBackend


class MultilingualE5SmallBackend(EmbeddingBackend):
    dimensions = 384
    privacy_allowed = True

    def __init__(
        self,
        *,
        name: str = "local_multilingual_e5_small",
        model: str = "intfloat/multilingual-e5-small",
    ) -> None:
        self.name = name
        self.model = model
        self._model: Any | None = None

    async def embed_query(self, text: str) -> list[float]:
        return await self._embed(f"query: {text}")

    async def embed_passage(self, text: str) -> list[float]:
        return await self._embed(f"passage: {text}")

    async def _embed(self, text: str) -> list[float]:
        return await asyncio.to_thread(self._embed_sync, text)

    def _embed_sync(self, text: str) -> list[float]:
        model = self._load_model()
        vector = model.encode(text, normalize_embeddings=True)
        array = np.asarray(vector, dtype=np.float32)
        if array.ndim != 1:
            array = array.reshape(-1)
        return array.tolist()

    def _load_model(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model)
        return self._model
