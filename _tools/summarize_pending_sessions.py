from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def _main() -> None:
    from server.background.session_summarizer import SessionSummarizer
    from server.shared.config import NodeConfig
    from server.shared.inference.embedding import create_embedding_backend
    from server.shared.inference.router import InferenceRouter
    from server.shared.memory import PostgresConversationSessionSummaryStore

    parser = argparse.ArgumentParser(
        description="Summarize closed Tomoko conversation sessions."
    )
    parser.add_argument(
        "--config",
        default=str(ROOT / "config" / "central_realtime.toml"),
        help="Path to TOML config.",
    )
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    config = NodeConfig.load(args.config)
    if config.inference.embedding_backend is None:
        raise SystemExit("embedding_backend is not configured")

    embedding_backend = create_embedding_backend(
        config.backends[config.inference.embedding_backend]
    )
    summarizer = SessionSummarizer(
        session_summary_store=PostgresConversationSessionSummaryStore(
            config.database.dsn
        ),
        router=InferenceRouter(config=config),
        embedding_backend=embedding_backend,
    )
    processed = await summarizer.process_pending(limit=args.limit)
    print(f"processed={processed}")


if __name__ == "__main__":
    asyncio.run(_main())
