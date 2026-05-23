from collections.abc import AsyncGenerator
from typing import Any

from server.shared.inference.backends.base import InferenceBackend


class InferenceRouterProto:
    async def select(self, role: str, preference: str = "latency") -> InferenceBackend: ...


class MockMonitor:
    def __init__(self, metrics: dict[str, Any] | None = None) -> None:
        self.metrics = metrics or {}
