from __future__ import annotations

import asyncio
from typing import Any

import numpy as np

from server.shared.inference.embedding.base import EmbeddingBackend


class SentenceTransformerEmbeddingBackend(EmbeddingBackend):
    privacy_allowed = True

    def __init__(
        self,
        *,
        name: str,
        model: str,
        dimensions: int,
        query_prefix: str = "",
        passage_prefix: str = "",
    ) -> None:
        self.name = name
        self.model = model
        self.dimensions = dimensions
        self.query_prefix = query_prefix
        self.passage_prefix = passage_prefix
        self._model: Any | None = None

    async def embed_query(self, text: str) -> list[float]:
        return await self._embed(f"{self.query_prefix}{text}")

    async def embed_passage(self, text: str) -> list[float]:
        return await self._embed(f"{self.passage_prefix}{text}")

    async def _embed(self, text: str) -> list[float]:
        return await asyncio.to_thread(self._embed_sync, text)

    def _embed_sync(self, text: str) -> list[float]:
        model = self._load_model()
        vector = model.encode(text, normalize_embeddings=True)
        array = np.asarray(vector, dtype=np.float32)
        if array.ndim != 1:
            array = array.reshape(-1)
        if array.shape[0] != self.dimensions:
            raise ValueError(
                f"embedding dimension mismatch model={self.model} "
                f"expected={self.dimensions} actual={array.shape[0]}"
            )
        return array.tolist()

    def _load_model(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model)
        return self._model


class MultilingualE5SmallBackend(SentenceTransformerEmbeddingBackend):
    def __init__(
        self,
        *,
        name: str = "local_multilingual_e5_small",
        model: str = "intfloat/multilingual-e5-small",
    ) -> None:
        super().__init__(
            name=name,
            model=model,
            dimensions=384,
            query_prefix="query: ",
            passage_prefix="passage: ",
        )


class BGEM3Backend(SentenceTransformerEmbeddingBackend):
    def __init__(
        self,
        *,
        name: str = "local_bge_m3",
        model: str = "BAAI/bge-m3",
        dimensions: int = 1024,
    ) -> None:
        if dimensions != 1024:
            raise ValueError(f"BGE-M3 requires 1024 dimensions, got {dimensions}")
        super().__init__(
            name=name,
            model=model,
            dimensions=dimensions,
        )
