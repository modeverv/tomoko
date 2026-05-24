from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def _main() -> None:
    from server.background.persona_updater import (
        LLMPersonaSnapshotExtractor,
        PersonaSnapshotUpdater,
    )
    from server.shared.config import NodeConfig
    from server.shared.inference.router import InferenceRouter
    from server.shared.persona import PostgresPersonaSnapshotStore

    parser = argparse.ArgumentParser(
        description="Update Tomoko persona lexicon and state snapshots."
    )
    parser.add_argument(
        "--config",
        default=str(ROOT / "config" / "central_realtime.toml"),
        help="Path to TOML config.",
    )
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Keep polling completed sessions instead of exiting after one batch.",
    )
    parser.add_argument(
        "--interval-sec",
        type=float,
        default=60.0,
        help="Polling interval used with --watch.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    )

    config = NodeConfig.load(args.config)
    updater = PersonaSnapshotUpdater(
        store=PostgresPersonaSnapshotStore(config.database.dsn),
        extractor=LLMPersonaSnapshotExtractor(router=InferenceRouter(config=config)),
    )

    if not args.watch:
        processed = await updater.process_completed_sessions(limit=args.limit)
        print(f"processed={processed}")
        return

    while True:
        processed = await updater.process_completed_sessions(limit=args.limit)
        print(f"processed={processed}")
        await asyncio.sleep(args.interval_sec)


if __name__ == "__main__":
    asyncio.run(_main())
