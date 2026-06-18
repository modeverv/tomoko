from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.shared.config import NodeConfig  # noqa: E402

CHANNEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


@dataclass(frozen=True, slots=True)
class NotifySample:
    sequence: int
    latency_ms: float
    notify_execute_ms: float
    receive_lag_after_execute_ms: float


@dataclass(frozen=True, slots=True)
class NotifySummary:
    samples: int
    dropped_warmup: int
    missing: int
    avg_ms: float
    min_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    notify_execute_avg_ms: float
    notify_execute_p95_ms: float
    receive_after_execute_avg_ms: float


def percentile(values: list[float], pct: float) -> float:
    if not values:
        raise ValueError("at least one value is required")
    if pct < 0 or pct > 100:
        raise ValueError("pct must be between 0 and 100")
    if len(values) == 1:
        return values[0]

    ordered = sorted(values)
    rank = (len(ordered) - 1) * (pct / 100)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def summarize_samples(
    samples: list[NotifySample],
    *,
    dropped_warmup: int,
    expected_samples: int,
) -> NotifySummary:
    if not samples:
        raise ValueError("at least one notify sample is required")

    latencies = [sample.latency_ms for sample in samples]
    execute_values = [sample.notify_execute_ms for sample in samples]
    receive_after_execute = [sample.receive_lag_after_execute_ms for sample in samples]
    return NotifySummary(
        samples=len(samples),
        dropped_warmup=dropped_warmup,
        missing=max(0, expected_samples - len(samples)),
        avg_ms=statistics.fmean(latencies),
        min_ms=min(latencies),
        p50_ms=percentile(latencies, 50),
        p95_ms=percentile(latencies, 95),
        p99_ms=percentile(latencies, 99),
        max_ms=max(latencies),
        notify_execute_avg_ms=statistics.fmean(execute_values),
        notify_execute_p95_ms=percentile(execute_values, 95),
        receive_after_execute_avg_ms=statistics.fmean(receive_after_execute),
    )


def validate_channel(value: str) -> str:
    if not CHANNEL_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "channel must be a PostgreSQL identifier up to 63 chars"
        )
    return value


async def measure_notify_latency(
    dsn: str,
    *,
    channel: str,
    samples: int,
    warmup: int,
    interval_ms: float,
    timeout_sec: float,
) -> tuple[NotifySummary, list[NotifySample]]:
    if samples <= 0:
        raise ValueError("samples must be greater than zero")
    if warmup < 0:
        raise ValueError("warmup must be zero or greater")

    total_messages = samples + warmup
    sent_by_sequence: dict[int, tuple[int, int]] = {}
    received_raw: list[tuple[int, int, int]] = []

    async with (
        await psycopg.AsyncConnection.connect(dsn, autocommit=True) as listener_conn,
        await psycopg.AsyncConnection.connect(dsn, autocommit=True) as producer_conn,
    ):
        await listener_conn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(channel)))

        async def collect_notifications() -> None:
            async for notify in listener_conn.notifies():
                received_ns = time.perf_counter_ns()
                payload = json.loads(notify.payload)
                sequence = int(payload["sequence"])
                sent_ns = int(payload["sent_perf_counter_ns"])
                if sequence >= warmup:
                    received_raw.append((sequence, sent_ns, received_ns))
                if len(received_raw) >= samples:
                    break

        collector = asyncio.create_task(collect_notifications())
        try:
            for sequence in range(total_messages):
                sent_ns = time.perf_counter_ns()
                payload = json.dumps(
                    {
                        "sequence": sequence,
                        "sent_perf_counter_ns": sent_ns,
                    },
                    separators=(",", ":"),
                )
                await producer_conn.execute("SELECT pg_notify(%s, %s)", (channel, payload))
                execute_done_ns = time.perf_counter_ns()
                sent_by_sequence[sequence] = (execute_done_ns, execute_done_ns - sent_ns)
                if interval_ms > 0:
                    await asyncio.sleep(interval_ms / 1000)

            await asyncio.wait_for(collector, timeout=timeout_sec)
        finally:
            collector.cancel()
            try:
                await collector
            except asyncio.CancelledError:
                pass

    received = [
        NotifySample(
            sequence=sequence - warmup,
            latency_ms=(received_ns - sent_ns) / 1_000_000,
            notify_execute_ms=sent_by_sequence[sequence][1] / 1_000_000,
            receive_lag_after_execute_ms=(received_ns - sent_by_sequence[sequence][0])
            / 1_000_000,
        )
        for sequence, sent_ns, received_ns in received_raw
        if sequence in sent_by_sequence
    ]

    return (
        summarize_samples(
            received,
            dropped_warmup=warmup,
            expected_samples=samples,
        ),
        received,
    )


def write_json_result(
    path: Path,
    *,
    config_path: Path,
    channel: str,
    summary: NotifySummary,
    samples: list[NotifySample],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "config_path": str(config_path),
        "channel": channel,
        "summary": asdict(summary),
        "samples": [asdict(sample) for sample in samples],
    }
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


def print_summary(summary: NotifySummary, *, output: Path) -> None:
    print("PostgreSQL LISTEN/NOTIFY latency")
    print(f"  samples: {summary.samples} (warmup dropped: {summary.dropped_warmup})")
    print(
        "  latency_ms: "
        f"avg={summary.avg_ms:.3f} "
        f"p50={summary.p50_ms:.3f} "
        f"p95={summary.p95_ms:.3f} "
        f"p99={summary.p99_ms:.3f} "
        f"min={summary.min_ms:.3f} "
        f"max={summary.max_ms:.3f}"
    )
    print(
        "  notify_execute_ms: "
        f"avg={summary.notify_execute_avg_ms:.3f} "
        f"p95={summary.notify_execute_p95_ms:.3f}"
    )
    print(f"  receive_after_execute_avg_ms: {summary.receive_after_execute_avg_ms:.3f}")
    print(f"  output: {output}")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure local PostgreSQL LISTEN/NOTIFY delivery latency.",
    )
    parser.add_argument("--config", type=Path, default=Path("config/central_realtime.toml"))
    parser.add_argument("--channel", type=validate_channel, default="tomoko_notify_latency_bench")
    parser.add_argument("--samples", type=int, default=500)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--interval-ms", type=float, default=1.0)
    parser.add_argument("--timeout-sec", type=float, default=5.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("logs/postgres-notify-latency.json"),
    )
    args = parser.parse_args()

    config = NodeConfig.load(args.config)
    summary, sample_rows = await measure_notify_latency(
        config.database.dsn,
        channel=args.channel,
        samples=args.samples,
        warmup=args.warmup,
        interval_ms=args.interval_ms,
        timeout_sec=args.timeout_sec,
    )
    write_json_result(
        args.output,
        config_path=args.config,
        channel=args.channel,
        summary=summary,
        samples=sample_rows,
    )
    print_summary(summary, output=args.output)


if __name__ == "__main__":
    asyncio.run(main())
