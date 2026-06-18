from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from server.shared.candidate import InMemoryCandidateStore
from server.thinker.arrival import ArrivalPrecomputer


class FastFailingRouter:
    async def select(self, role: str, preference: str = "latency") -> object:
        del role, preference
        raise RuntimeError("fake backend disabled")


@pytest.mark.perf
async def test_arrival_precompute_fake_backend_under_20ms() -> None:
    precomputer = ArrivalPrecomputer(
        store=InMemoryCandidateStore(),
        router=FastFailingRouter(),  # type: ignore[arg-type]
    )

    started = time.perf_counter()
    candidate = await precomputer.precompute_once(
        now=datetime(2026, 5, 24, 19, 0, tzinfo=UTC),
        device_id="kitchen",
    )
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert elapsed_ms < 20
    assert candidate.behavior == "wait_silent"
