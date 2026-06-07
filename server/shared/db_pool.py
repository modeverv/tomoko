from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
from psycopg_pool import AsyncConnectionPool

_POOLS: dict[str, AsyncConnectionPool] = {}
_POOL_LOCKS: dict[str, asyncio.Lock] = {}


def _pool_lock(dsn: str) -> asyncio.Lock:
    lock = _POOL_LOCKS.get(dsn)
    if lock is None:
        lock = asyncio.Lock()
        _POOL_LOCKS[dsn] = lock
    return lock


async def get_async_connection_pool(dsn: str) -> AsyncConnectionPool:
    pool = _POOLS.get(dsn)
    if pool is not None:
        return pool

    async with _pool_lock(dsn):
        pool = _POOLS.get(dsn)
        if pool is not None:
            return pool

        pool = AsyncConnectionPool(
            dsn,
            min_size=1,
            max_size=8,
            open=False,
        )
        await pool.open(wait=True)
        _POOLS[dsn] = pool
        return pool


@asynccontextmanager
async def pooled_connection(dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    pool = await get_async_connection_pool(dsn)
    async with pool.connection() as conn:
        yield conn


async def close_async_connection_pools() -> None:
    pools = list(_POOLS.values())
    _POOLS.clear()
    _POOL_LOCKS.clear()
    for pool in pools:
        await pool.close()
