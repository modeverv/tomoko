from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from psycopg_pool import AsyncConnectionPool


@dataclass(frozen=True, slots=True)
class DbSettings:
    dsn: str
    min_size: int = 1
    max_size: int = 4
    open: bool = False


def default_dsn() -> str:
    return os.environ.get(
        "TOMOKO_DATABASE_URL",
        "postgresql://tomoko:tomoko@localhost:5432/tomoko",
    )


def create_pool(settings: DbSettings | None = None) -> AsyncConnectionPool[Any]:
    resolved = settings or DbSettings(dsn=default_dsn())
    return AsyncConnectionPool(
        conninfo=resolved.dsn,
        min_size=resolved.min_size,
        max_size=resolved.max_size,
        open=resolved.open,
    )
