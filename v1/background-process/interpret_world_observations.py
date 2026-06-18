from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def async_main(argv: list[str] | None = None) -> int:
    from server.shared.config import NodeConfig
    from server.shared.inference.router import InferenceRouter
    from server.world_observations.interpreter import (
        PostgresPersonaSnapshotReader,
        WorldObservationInterpreter,
    )
    from server.world_observations.store import PostgresWorldObservationStore

    parser = argparse.ArgumentParser(
        description="Interpret normalized world observation items."
    )
    parser.add_argument("--config", default="config/central_realtime.toml")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--interval-sec", type=float, default=300.0)
    args = parser.parse_args(argv)

    config = NodeConfig.load(args.config)
    router = InferenceRouter(config=config)
    backend = await router.select("diary", "privacy")
    interpreter = WorldObservationInterpreter(
        store=PostgresWorldObservationStore(config.database.dsn),
        backend=backend,
        persona_reader=PostgresPersonaSnapshotReader(config.database.dsn),
    )

    if args.watch:
        while True:
            result = await interpreter.interpret_once(limit=args.limit)
            print(_format_result(result))
            await asyncio.sleep(args.interval_sec)

    result = await interpreter.interpret_once(limit=args.limit)
    print(_format_result(result))
    return 0


def _format_result(result) -> str:
    return (
        "world_observation_interpret "
        f"interpreted={result.interpreted_count} "
        f"error_count={result.error_count}"
    )


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
