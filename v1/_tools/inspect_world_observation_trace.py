from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def async_main(argv: list[str] | None = None) -> int:
    from server.shared.config import NodeConfig

    parser = argparse.ArgumentParser(description="Inspect world observation trace.")
    parser.add_argument("--config", default="config/central_realtime.toml")
    parser.add_argument("--document-path")
    parser.add_argument("--candidate-id")
    parser.add_argument("--conversation-log-id")
    args = parser.parse_args(argv)

    if not any([args.document_path, args.candidate_id, args.conversation_log_id]):
        parser.error("one of --document-path, --candidate-id, --conversation-log-id is required")

    config = NodeConfig.load(args.config)
    query, params = _build_query(args)
    async with await psycopg.AsyncConnection.connect(config.database.dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(query, params)
            rows = await cur.fetchall()

    for row in rows:
        print(
            "\n".join(
                [
                    f"document_id={row[0]} path={row[1]} observed_at={row[4]}",
                    f"item_id={row[5]} topic={row[6]} title={row[7]}",
                    f"interpretation_id={row[12]} interest={row[16]} relevance={row[15]}",
                    f"candidate_id={row[23]} diary_id={row[24]} conversation_log_id={row[25]}",
                    f"text={row[20]}",
                    "",
                ]
            )
        )
    return 0


def _build_query(args) -> tuple[str, tuple[object, ...]]:
    base = "SELECT * FROM world_observation_trace"
    if args.document_path:
        return f"{base} WHERE raw_file_path = %s ORDER BY observed_at DESC", (
            args.document_path,
        )
    if args.candidate_id:
        return (
            f"{base} WHERE utterance_candidate_id = %s ORDER BY observed_at DESC",
            (args.candidate_id,),
        )
    return (
        f"{base} WHERE conversation_log_id = %s ORDER BY observed_at DESC",
        (args.conversation_log_id,),
    )


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
