from __future__ import annotations

import pytest

from server.shared import db_pool


class FakeAsyncConnectionPool:
    instances: list[FakeAsyncConnectionPool] = []

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int,
        max_size: int,
        open: bool,
    ) -> None:
        self.dsn = dsn
        self.min_size = min_size
        self.max_size = max_size
        self.open_arg = open
        self.opened = False
        self.closed = False
        FakeAsyncConnectionPool.instances.append(self)

    async def open(self, *, wait: bool) -> None:
        assert wait is True
        self.opened = True

    async def close(self) -> None:
        self.closed = True


@pytest.mark.unit
async def test_get_async_connection_pool_reuses_pool_per_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await db_pool.close_async_connection_pools()
    FakeAsyncConnectionPool.instances.clear()
    monkeypatch.setattr(db_pool, "AsyncConnectionPool", FakeAsyncConnectionPool)

    first = await db_pool.get_async_connection_pool("postgresql://example")
    second = await db_pool.get_async_connection_pool("postgresql://example")

    assert first is second
    assert len(FakeAsyncConnectionPool.instances) == 1
    assert first.min_size == 1
    assert first.max_size == 8
    assert first.open_arg is False
    assert first.opened is True


@pytest.mark.unit
async def test_close_async_connection_pools_closes_all_pools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await db_pool.close_async_connection_pools()
    FakeAsyncConnectionPool.instances.clear()
    monkeypatch.setattr(db_pool, "AsyncConnectionPool", FakeAsyncConnectionPool)

    first = await db_pool.get_async_connection_pool("postgresql://one")
    second = await db_pool.get_async_connection_pool("postgresql://two")

    await db_pool.close_async_connection_pools()

    assert first.closed is True
    assert second.closed is True
    assert db_pool._POOLS == {}
