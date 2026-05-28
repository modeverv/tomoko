from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def _main() -> None:
    from server.shared.config import NodeConfig
    from server.shared.inference.embedding import create_embedding_backend
    from server.shared.memory import PostgresConversationMemoryStore

    parser = argparse.ArgumentParser(
        description="Backfill embeddings for completed Tomoko conversation turns."
    )
    parser.add_argument(
        "--config",
        default=str(ROOT / "config" / "central_realtime.toml"),
        help="Path to TOML config.",
    )
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Keep polling missing turn embeddings instead of exiting after one batch.",
    )
    parser.add_argument(
        "--interval-sec",
        type=float,
        default=30.0,
        help="Polling interval used with --watch.",
    )
    args = parser.parse_args()

    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    )

    config = NodeConfig.load(args.config)
    if config.inference.embedding_backend is None:
        raise SystemExit("embedding_backend is not configured")

    embedding_backend = create_embedding_backend(
        config.backends[config.inference.embedding_backend]
    )
    store = PostgresConversationMemoryStore(config.database.dsn)

    if not args.watch:
        embedded = await store.embed_missing_turns(
            embedding_backend=embedding_backend,
            limit=args.limit,
        )
        print(f"embedded={embedded}")
        return

    while True:
        embedded = await store.embed_missing_turns(
            embedding_backend=embedding_backend,
            limit=args.limit,
        )
        print(f"embedded={embedded}")
        await asyncio.sleep(args.interval_sec)


if __name__ == "__main__":
    asyncio.run(_main())
