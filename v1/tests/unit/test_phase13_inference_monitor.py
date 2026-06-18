from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from server.shared.inference.backends.base import InferenceBackend
from server.shared.inference.monitor import (
    BackendHealthMonitor,
    InferenceMetricSample,
    InMemoryInferenceMetricStore,
)


class FakeBackend(InferenceBackend):
    name = "local"
    privacy_allowed = True

    async def chat_stream(self, system_prompt: str, messages: list[dict[str, str]]):
        del system_prompt, messages
        yield "pong"


class FailingBackend(InferenceBackend):
    name = "broken"
    privacy_allowed = True

    async def chat_stream(self, system_prompt: str, messages: list[dict[str, str]]):
        del system_prompt, messages
        raise RuntimeError("boom")
        yield ""


@pytest.mark.unit
async def test_metric_store_returns_latest_sample() -> None:
    store = InMemoryInferenceMetricStore()
    older = InferenceMetricSample(
        backend_name="local",
        task_type="conversation",
        latency_ms=100,
        measured_at=datetime(2026, 5, 24, 10, 0, tzinfo=UTC),
    )
    latest = InferenceMetricSample(
        backend_name="local",
        task_type="conversation",
        latency_ms=80,
        measured_at=older.measured_at + timedelta(minutes=1),
    )

    await store.write_sample(latest)
    await store.write_sample(older)

    assert await store.latest("local") == latest
    assert await store.latest("missing") is None


@pytest.mark.unit
async def test_health_monitor_records_latency_sample() -> None:
    store = InMemoryInferenceMetricStore()
    monitor = BackendHealthMonitor(store=store)

    sample = await monitor.probe_backend(FakeBackend(), task_type="conversation")

    assert sample.backend_name == "local"
    assert sample.task_type == "conversation"
    assert sample.error is None
    assert sample.latency_ms is not None
    assert await monitor.latest("local") == sample


@pytest.mark.unit
async def test_health_monitor_records_probe_failure_without_raising() -> None:
    store = InMemoryInferenceMetricStore()
    monitor = BackendHealthMonitor(store=store)

    sample = await monitor.probe_backend(FailingBackend(), task_type="conversation")

    assert sample.backend_name == "broken"
    assert sample.latency_ms is None
    assert sample.error == "RuntimeError"
    assert await monitor.latest("broken") == sample
