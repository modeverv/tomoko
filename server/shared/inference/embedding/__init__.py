from __future__ import annotations

from server.shared.config import BackendSpec
from server.shared.inference.embedding.base import EmbeddingBackend
from server.shared.inference.embedding.sentence_transformer import (
    BGEM3Backend,
    MultilingualE5SmallBackend,
)


def create_embedding_backend(spec: BackendSpec) -> EmbeddingBackend:
    if spec.type == "multilingual_e5_small":
        return MultilingualE5SmallBackend(
            name=spec.name,
            model=spec.model or "intfloat/multilingual-e5-small",
        )
    if spec.type == "bge_m3":
        return BGEM3Backend(
            name=spec.name,
            model=spec.model or "BAAI/bge-m3",
            dimensions=spec.dimensions or 1024,
        )
    raise ValueError(f"unsupported embedding backend type: {spec.type}")


__all__ = [
    "BGEM3Backend",
    "EmbeddingBackend",
    "MultilingualE5SmallBackend",
    "create_embedding_backend",
]
