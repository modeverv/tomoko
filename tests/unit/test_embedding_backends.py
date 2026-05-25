from __future__ import annotations

import pytest

from server.shared.config import BackendSpec
from server.shared.inference.embedding import BGEM3Backend, create_embedding_backend


@pytest.mark.unit
def test_create_bge_m3_embedding_backend_from_config() -> None:
    backend = create_embedding_backend(
        BackendSpec(
            name="local_bge_m3",
            type="bge_m3",
            model="BAAI/bge-m3",
            dimensions=1024,
        )
    )

    assert isinstance(backend, BGEM3Backend)
    assert backend.name == "local_bge_m3"
    assert backend.model == "BAAI/bge-m3"
    assert backend.dimensions == 1024
    assert backend.privacy_allowed is True


@pytest.mark.unit
def test_bge_m3_rejects_wrong_dimension_config() -> None:
    with pytest.raises(ValueError, match="BGE-M3"):
        create_embedding_backend(
            BackendSpec(
                name="bad_bge_m3",
                type="bge_m3",
                model="BAAI/bge-m3",
                dimensions=384,
            )
        )
