from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from server.shared.models import utc_now

ReadinessCheck = Callable[[], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class Heartbeat:
    process_name: str
    process_kind: str
    status: str = "running"
    details: dict[str, Any] = field(default_factory=dict)
    last_seen_at: datetime = field(default_factory=utc_now)


class HeartbeatWriter:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def write(self, heartbeat: Heartbeat) -> None:
        query = """
            INSERT INTO v2_process_heartbeats
                (process_name, process_kind, status, details, last_seen_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (process_name) DO UPDATE SET
                process_kind = EXCLUDED.process_kind,
                status = EXCLUDED.status,
                details = EXCLUDED.details,
                last_seen_at = EXCLUDED.last_seen_at
        """
        async with self._pool.connection() as conn:
            await conn.execute(
                query,
                (
                    heartbeat.process_name,
                    heartbeat.process_kind,
                    heartbeat.status,
                    heartbeat.details,
                    heartbeat.last_seen_at,
                ),
            )


async def wait_until_ready(
    checks: list[ReadinessCheck],
    *,
    poll_interval_sec: float = 0.2,
    timeout_sec: float = 10.0,
) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_sec
    while asyncio.get_running_loop().time() < deadline:
        results = [await check() for check in checks]
        if all(results):
            return True
        await asyncio.sleep(poll_interval_sec)
    return False


async def run_until_cancelled(
    heartbeat_writer: HeartbeatWriter,
    heartbeat: Heartbeat,
    *,
    interval_sec: float = 5.0,
) -> None:
    try:
        while True:
            await heartbeat_writer.write(heartbeat)
            await asyncio.sleep(interval_sec)
    except asyncio.CancelledError:
        await heartbeat_writer.write(
            Heartbeat(
                process_name=heartbeat.process_name,
                process_kind=heartbeat.process_kind,
                status="stopped",
                details=heartbeat.details,
            )
        )
        raise
