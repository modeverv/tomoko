from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from server.shared.config import NodeConfig
from server.tools.initiative_motivation_sandbox import (
    arrival_lifecycle,
    candidate_lifecycle,
    write_json,
)

UTTERANCE_SELECT = """
SELECT
    id,
    seed,
    generated_text,
    priority,
    urgent,
    created_at,
    expires_at,
    spoken_at,
    dismissed_at,
    maturity,
    source,
    context_tags,
    metadata_json
FROM utterance_candidates
ORDER BY created_at DESC
LIMIT %s
"""


ARRIVAL_SELECT = """
SELECT
    id,
    device_id,
    computed_at,
    valid_until,
    context_snapshot,
    behavior,
    utterance_text,
    used_at
FROM arrival_candidates
ORDER BY computed_at DESC
LIMIT %s
"""


async def export_candidates(
    *,
    dsn: str,
    limit: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    observed_at = now or datetime.now(UTC)
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(UTTERANCE_SELECT, (limit,))
            utterance_rows = await cur.fetchall()
            await cur.execute(ARRIVAL_SELECT, (limit,))
            arrival_rows = await cur.fetchall()

    utterances = [
        _utterance_row_to_json(row, observed_at) for row in utterance_rows
    ]
    arrivals = [_arrival_row_to_json(row, observed_at) for row in arrival_rows]
    return {
        "schema_version": 1,
        "exported_at": observed_at.astimezone().isoformat(timespec="milliseconds"),
        "utterance_candidates": utterances,
        "arrival_candidates": arrivals,
        "summary": {
            "utterance_count": len(utterances),
            "arrival_count": len(arrivals),
            "utterance_lifecycle_counts": _count_by(utterances, "lifecycle"),
            "arrival_lifecycle_counts": _count_by(arrivals, "lifecycle"),
        },
    }


def _utterance_row_to_json(row: tuple[Any, ...], now: datetime) -> dict[str, Any]:
    (
        candidate_id,
        seed,
        generated_text,
        priority,
        urgent,
        created_at,
        expires_at,
        spoken_at,
        dismissed_at,
        maturity,
        source,
        context_tags,
        metadata_json,
    ) = row
    payload = {
        "id": str(candidate_id),
        "seed": str(seed),
        "generated_text": generated_text,
        "priority": float(priority),
        "urgent": bool(urgent),
        "created_at": _iso(created_at),
        "expires_at": _iso(expires_at),
        "spoken_at": _iso_or_none(spoken_at),
        "dismissed_at": _iso_or_none(dismissed_at),
        "maturity": int(maturity),
        "source": str(source),
        "context_tags": list(context_tags or ()),
        "metadata_json": dict(metadata_json or {}),
    }
    payload["lifecycle"] = candidate_lifecycle(payload, now)
    return payload


def _arrival_row_to_json(row: tuple[Any, ...], now: datetime) -> dict[str, Any]:
    (
        candidate_id,
        device_id,
        computed_at,
        valid_until,
        context_snapshot,
        behavior,
        utterance_text,
        used_at,
    ) = row
    payload = {
        "id": str(candidate_id),
        "device_id": device_id,
        "computed_at": _iso(computed_at),
        "valid_until": _iso(valid_until),
        "context_snapshot": dict(context_snapshot or {}),
        "behavior": str(behavior),
        "utterance_text": utterance_text,
        "used_at": _iso_or_none(used_at),
    }
    payload["lifecycle"] = arrival_lifecycle(payload, now)
    return payload


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone().isoformat(timespec="milliseconds")
    return str(value)


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return _iso(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/central_realtime.toml")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument(
        "--output",
        default=None,
        help="Default: reports/initiative-motivation/candidates-<timestamp>.json",
    )
    return parser.parse_args()


async def _main() -> None:
    args = parse_args()
    config = NodeConfig.load(args.config)
    payload = await export_candidates(dsn=config.database.dsn, limit=args.limit)
    if args.output is None:
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        output = Path(f"reports/initiative-motivation/candidates-{stamp}.json")
    else:
        output = Path(args.output)
    write_json(output, payload)
    print(json.dumps({"output": str(output), **payload["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(_main())

