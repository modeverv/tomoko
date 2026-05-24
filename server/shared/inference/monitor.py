from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

import psycopg

from server.shared.inference.backends.base import InferenceBackend


class InferenceRouterProto:
    async def select(self, role: str, preference: str = "latency") -> object: ...


class MockMonitor:
    def __init__(self, metrics: dict[str, Any] | None = None) -> None:
        self.metrics = metrics or {}

    async def latest(self, backend_name: str) -> Any | None:
        return self.metrics.get(backend_name)


@dataclass(frozen=True)
class InferenceMetricSample:
    backend_name: str
    task_type: str
    latency_ms: float | None = None
    error: str | None = None
    measured_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    id: UUID = field(default_factory=uuid4)

    @classmethod
    def from_db_row(cls, row: tuple[object, ...]) -> InferenceMetricSample:
        (
            metric_id,
            backend_name,
            task_type,
            latency_ms,
            error,
            measured_at,
        ) = row
        return cls(
            id=_as_uuid(metric_id),
            backend_name=str(backend_name),
            task_type=str(task_type),
            latency_ms=float(latency_ms) if latency_ms is not None else None,
            error=str(error) if error is not None else None,
            measured_at=_as_datetime(measured_at),
        )


class InferenceMetricStore(Protocol):
    async def write_sample(self, sample: InferenceMetricSample) -> None: ...

    async def latest(self, backend_name: str) -> InferenceMetricSample | None: ...


class InMemoryInferenceMetricStore:
    def __init__(self) -> None:
        self.samples: list[InferenceMetricSample] = []

    async def write_sample(self, sample: InferenceMetricSample) -> None:
        self.samples.append(sample)

    async def latest(self, backend_name: str) -> InferenceMetricSample | None:
        matches = [
            sample for sample in self.samples if sample.backend_name == backend_name
        ]
        if not matches:
            return None
        return max(matches, key=lambda sample: sample.measured_at)


class PostgresInferenceMetricStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def write_sample(self, sample: InferenceMetricSample) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO inference_metrics (
                        id,
                        backend_name,
                        task_type,
                        latency_ms,
                        error,
                        measured_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        sample.id,
                        sample.backend_name,
                        sample.task_type,
                        sample.latency_ms,
                        sample.error,
                        sample.measured_at,
                    ),
                )

    async def latest(self, backend_name: str) -> InferenceMetricSample | None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        id,
                        backend_name,
                        task_type,
                        latency_ms,
                        error,
                        measured_at
                    FROM inference_metrics
                    WHERE backend_name = %s
                    ORDER BY measured_at DESC
                    LIMIT 1
                    """,
                    (backend_name,),
                )
                row = await cur.fetchone()
        if row is None:
            return None
        return InferenceMetricSample.from_db_row(row)


class BackendHealthMonitor:
    def __init__(self, *, store: InferenceMetricStore) -> None:
        self.store = store

    async def latest(self, backend_name: str) -> InferenceMetricSample | None:
        return await self.store.latest(backend_name)

    async def probe_backend(
        self,
        backend: InferenceBackend,
        *,
        task_type: str,
    ) -> InferenceMetricSample:
        started_at = time.perf_counter()
        try:
            warm_up = getattr(backend, "warm_up", None)
            if warm_up is not None:
                await warm_up()
            else:
                async for _ in backend.chat_stream(
                    "短く返事してください。",
                    [{"role": "user", "content": "ping"}],
                ):
                    break
            sample = InferenceMetricSample(
                backend_name=backend.name,
                task_type=task_type,
                latency_ms=(time.perf_counter() - started_at) * 1000,
            )
        except Exception as exc:
            sample = InferenceMetricSample(
                backend_name=backend.name,
                task_type=task_type,
                latency_ms=None,
                error=type(exc).__name__,
            )
        await self.store.write_sample(sample)
        return sample


def _as_uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(f"Expected datetime value, got {type(value)!r}")
