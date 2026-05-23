from __future__ import annotations

from typing import Any


class InferenceRouterProto:
    async def select(self, role: str, preference: str = "latency") -> object: ...


class MockMonitor:
    def __init__(self, metrics: dict[str, Any] | None = None) -> None:
        self.metrics = metrics or {}

    async def latest(self, backend_name: str) -> Any | None:
        return self.metrics.get(backend_name)
