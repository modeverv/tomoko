from __future__ import annotations

import pytest

from server.session_key_helpers import candidate_request_id


@pytest.mark.unit
def test_candidate_request_id_preserves_existing_format() -> None:
    assert candidate_request_id("initiative", 1) == "initiative-1"
    assert candidate_request_id("arrival", 2) == "arrival-2"
