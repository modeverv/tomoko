from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_CONFIG = ROOT / "config" / "central_realtime.toml"


async def main() -> None:
    from server.shared.config import NodeConfig
    from server.shared.inference.embedding import create_embedding_backend
    from server.shared.memory import PostgresConversationMemoryStore

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    config = NodeConfig.load(args.config)
    if config.inference.embedding_backend is None:
        raise SystemExit("embedding_backend is not configured")

    embedding_backend = create_embedding_backend(
        config.backends[config.inference.embedding_backend]
    )
    store = PostgresConversationMemoryStore(config.database.dsn)
    count = await store.embed_missing_turns(
        embedding_backend=embedding_backend,
        limit=args.limit,
    )
    print(f"embedded conversation turns: {count}")


if __name__ == "__main__":
    asyncio.run(main())
