from __future__ import annotations

import argparse
import asyncio
import html
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from server.shared.candidate import CandidateSeed, CandidateStore, ThinkerSourceContext
from server.shared.models import UserContextSnapshot
from server.shared.perception import (
    PostgresHumanActivityObservationStore,
    PostgresHumanPresenceObservationStore,
    PostgresScreenActivityObservationStore,
    PostgresUserContextSnapshotStore,
)
from server.thinker.perception.context_snapshot import UserContextSnapshotBuilder
from server.thinker.sources.base import InformationSource
from server.thinker.sources.context_snapshot import ActivityContextSource, ScreenContextSource

logger = logging.getLogger(__name__)
SleepFunc = Callable[[float], Awaitable[None]]


class SnapshotBuilder(Protocol):
    async def build_once(self, *, now: datetime): ...


@dataclass(frozen=True)
class Thinker2RunResult:
    snapshot_readiness: str
    snapshot_summary: str
    candidate_generated_count: int
    candidate_inserted_count: int
    queue_depths: dict[str, int] = field(default_factory=dict)
    inference_latency_ms: dict[str, float] = field(default_factory=dict)
    skipped_stale_frame_count: int = 0
    skipped_backlog_frame_count: int = 0
    elapsed_ms: float = 0.0


class Thinker2Process:
    def __init__(
        self,
        *,
        snapshot_builder: SnapshotBuilder,
        candidate_store: CandidateStore,
        candidate_sources: Sequence[InformationSource],
    ) -> None:
        self.snapshot_builder = snapshot_builder
        self.candidate_store = candidate_store
        self.candidate_sources = tuple(candidate_sources)

    async def run_once(self, *, now: datetime | None = None) -> Thinker2RunResult:
        observed_at = now or datetime.now(UTC)
        started_at = time.perf_counter()
        build_result = await self.snapshot_builder.build_once(now=observed_at)
        generated: list[CandidateSeed] = []
        source_context = ThinkerSourceContext(observed_at=observed_at)
        for source in self.candidate_sources:
            generated.extend(await source.collect(source_context))

        inserted_count = 0
        for seed in generated:
            inserted = await self.candidate_store.insert_seed_candidate_once(
                seed,
                created_at=observed_at,
            )
            if inserted is not None:
                inserted_count += 1

        snapshot: UserContextSnapshot = build_result.snapshot
        trace = build_result.trace
        result = Thinker2RunResult(
            snapshot_readiness=snapshot.interaction_readiness,
            snapshot_summary=snapshot.context_summary,
            candidate_generated_count=len(generated),
            candidate_inserted_count=inserted_count,
            queue_depths={"candidate_sources": len(self.candidate_sources)},
            inference_latency_ms={"snapshot_build": float(trace.elapsed_ms)},
            skipped_stale_frame_count=0,
            skipped_backlog_frame_count=0,
            elapsed_ms=(time.perf_counter() - started_at) * 1000,
        )
        logger.info(
            "thinker2 run_once readiness=%s candidate_generated_count=%s "
            "candidate_inserted_count=%s queue_depths=%s inference_latency_ms=%s "
            "skipped_stale_frame_count=%s skipped_backlog_frame_count=%s elapsed_ms=%.1f",
            result.snapshot_readiness,
            result.candidate_generated_count,
            result.candidate_inserted_count,
            result.queue_depths,
            result.inference_latency_ms,
            result.skipped_stale_frame_count,
            result.skipped_backlog_frame_count,
            result.elapsed_ms,
        )
        return result


async def run_watch(
    process: Thinker2Process,
    *,
    interval_sec: float,
    sleep: SleepFunc = asyncio.sleep,
) -> None:
    while True:
        await process.run_once()
        await sleep(interval_sec)


def build_default_thinker2(config_path: str) -> Thinker2Process:
    from server.shared.calendar import PostgresCalendarEventStore
    from server.shared.candidate import PostgresCandidateStore
    from server.shared.config import NodeConfig
    from server.world_observations.store import PostgresWorldObservationStore

    config = NodeConfig.load(config_path)
    snapshot_store = PostgresUserContextSnapshotStore(config.database.dsn)
    snapshot_builder = UserContextSnapshotBuilder(
        snapshot_store=snapshot_store,
        presence_store=PostgresHumanPresenceObservationStore(config.database.dsn),
        activity_store=PostgresHumanActivityObservationStore(config.database.dsn),
        screen_store=PostgresScreenActivityObservationStore(config.database.dsn),
        calendar_store=PostgresCalendarEventStore(config.database.dsn),
        world_store=PostgresWorldObservationStore(config.database.dsn),
        device_id=config.node.device_id,
    )
    return Thinker2Process(
        snapshot_builder=snapshot_builder,
        candidate_store=PostgresCandidateStore(config.database.dsn),
        candidate_sources=[
            ScreenContextSource(snapshot_store=snapshot_store),
            ActivityContextSource(snapshot_store=snapshot_store),
        ],
    )


async def async_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Tomoko thinker2 process.")
    parser.add_argument("--config", default="config/central_realtime.toml")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval-sec", type=float, default=60.0)
    parser.add_argument("--inspection-output", default="reports/thinker2/latest.html")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    )
    await ensure_default_thinker2_schemas(args.config)
    process = build_default_thinker2(args.config)
    if args.watch:
        await run_watch(process, interval_sec=args.interval_sec)
        return 0

    result = await process.run_once()
    output_path = Path(args.inspection_output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_thinker2_inspection_html(result), encoding="utf-8")
    print(
        "thinker2_once "
        f"readiness={result.snapshot_readiness} "
        f"candidate_generated={result.candidate_generated_count} "
        f"candidate_inserted={result.candidate_inserted_count} "
        f"inspection={output_path}"
    )
    return 0


def render_thinker2_inspection_html(result: Thinker2RunResult) -> str:
    rows = {
        "snapshot_readiness": result.snapshot_readiness,
        "snapshot_summary": result.snapshot_summary,
        "candidate_generated_count": result.candidate_generated_count,
        "candidate_inserted_count": result.candidate_inserted_count,
        "queue_depths": result.queue_depths,
        "inference_latency_ms": result.inference_latency_ms,
        "skipped_stale_frame_count": result.skipped_stale_frame_count,
        "skipped_backlog_frame_count": result.skipped_backlog_frame_count,
        "elapsed_ms": round(result.elapsed_ms, 1),
    }
    body = "\n".join(
        "<tr>"
        f"<th>{html.escape(str(key))}</th>"
        f"<td>{html.escape(str(value))}</td>"
        "</tr>"
        for key, value in rows.items()
    )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>thinker2 inspection</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:24px;}"
        "table{border-collapse:collapse;max-width:960px;width:100%;}"
        "th,td{border:1px solid #ddd;padding:8px;text-align:left;}"
        "th{width:260px;background:#f6f8fa;}</style></head>"
        "<body><h1>thinker2 inspection</h1><table>"
        f"{body}</table></body></html>\n"
    )


async def ensure_default_thinker2_schemas(config_path: str) -> None:
    import psycopg

    from server.shared.config import NodeConfig
    from server.shared.perception import (
        HUMAN_ACTIVITY_OBSERVATIONS_SCHEMA_SQL,
        HUMAN_PRESENCE_OBSERVATIONS_SCHEMA_SQL,
        PERCEPTION_FRAMES_SCHEMA_SQL,
        SCREEN_ACTIVITY_OBSERVATIONS_SCHEMA_SQL,
        USER_CONTEXT_SNAPSHOTS_SCHEMA_SQL,
    )

    config = NodeConfig.load(config_path)
    async with await psycopg.AsyncConnection.connect(config.database.dsn) as conn:
        async with conn.cursor() as cur:
            for ddl in (
                PERCEPTION_FRAMES_SCHEMA_SQL,
                HUMAN_PRESENCE_OBSERVATIONS_SCHEMA_SQL,
                HUMAN_ACTIVITY_OBSERVATIONS_SCHEMA_SQL,
                SCREEN_ACTIVITY_OBSERVATIONS_SCHEMA_SQL,
                USER_CONTEXT_SNAPSHOTS_SCHEMA_SQL,
            ):
                await cur.execute(ddl)


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
