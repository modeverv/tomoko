from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any
from uuid import uuid4

import numpy as np

from server.shared.inference.embedding.base import EmbeddingBackend
from server.shared.inference.trace import trace_backend_call


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
        return await self._embed(f"{self.query_prefix}{text}", role="embedding_query")

    async def embed_passage(self, text: str) -> list[float]:
        return await self._embed(f"{self.passage_prefix}{text}", role="embedding_passage")

    async def _embed(self, text: str, *, role: str) -> list[float]:
        request_id = str(uuid4())
        started_at = perf_counter()
        trace_backend_call(
            event="start",
            kind="embedding",
            role=role,
            backend=self.name,
            model=self.model,
            request_id=request_id,
            queue_key="local_embedding",
            text_len=len(text),
        )
        try:
            embedding = await asyncio.to_thread(self._embed_sync, text)
        except Exception as exc:
            trace_backend_call(
                event="error",
                kind="embedding",
                role=role,
                backend=self.name,
                model=self.model,
                request_id=request_id,
                queue_key="local_embedding",
                total_ms=_elapsed_ms(started_at),
                error=type(exc).__name__,
            )
            raise
        trace_backend_call(
            event="done",
            kind="embedding",
            role=role,
            backend=self.name,
            model=self.model,
            request_id=request_id,
            queue_key="local_embedding",
            total_ms=_elapsed_ms(started_at),
            dimensions=len(embedding),
        )
        return embedding

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


def _elapsed_ms(started_at: float) -> float:
    return (perf_counter() - started_at) * 1000
