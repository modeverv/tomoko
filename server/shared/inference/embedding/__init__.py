from __future__ import annotations

from server.shared.config import BackendSpec
from server.shared.inference.embedding.base import EmbeddingBackend
from server.shared.inference.embedding.e5 import MultilingualE5SmallBackend


def create_embedding_backend(spec: BackendSpec) -> EmbeddingBackend:
    if spec.type == "multilingual_e5_small":
        return MultilingualE5SmallBackend(
            name=spec.name,
            model=spec.model or "intfloat/multilingual-e5-small",
        )
    raise ValueError(f"unsupported embedding backend type: {spec.type}")


__all__ = ["EmbeddingBackend", "MultilingualE5SmallBackend", "create_embedding_backend"]
