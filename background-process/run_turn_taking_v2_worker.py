from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.background.turn_taking_v2_worker import TurnTakingV2Worker
from server.shared.config import NodeConfig


async def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Turn-taking v2 shadow lane background worker."
    )
    parser.add_argument(
        "--config",
        default=str(ROOT / "config" / "central_realtime.toml"),
        help="Path to TOML config.",
    )
    parser.add_argument(
        "--recovery-interval-sec",
        type=float,
        default=5.0,
        help="Interval for recovery polling in seconds.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    )

    config = NodeConfig.load(args.config)
    dsn = config.database.dsn
    from server.shared.inference.router import InferenceRouter
    router = InferenceRouter(config=config)

    worker = TurnTakingV2Worker(dsn, router=router)
    await worker.run(recovery_interval_sec=args.recovery_interval_sec)


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        sys.exit(0)
