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
    from server.world_observations.ingest import WorldObservationIngestor
    from server.world_observations.normalizer import WorldObservationNormalizer
    from server.world_observations.store import PostgresWorldObservationStore

    parser = argparse.ArgumentParser(description="Ingest world observation Markdown.")
    parser.add_argument("--config", default="config/central_realtime.toml")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--path", default="informations/work")
    parser.add_argument("--archive-root", default="informations/archived")
    parser.add_argument("--failed-root", default="informations/failed")
    args = parser.parse_args(argv)

    if not args.once and not args.dry_run:
        parser.error("use --once or --dry-run")

    config = NodeConfig.load(args.config)
    router = InferenceRouter(config=config)
    backend = await router.select("memory_extraction", "privacy")
    ingestor = WorldObservationIngestor(
        store=PostgresWorldObservationStore(config.database.dsn),
        normalizer=WorldObservationNormalizer(backend=backend, max_retries=0),
        archive_root=args.archive_root,
        failed_root=args.failed_root,
    )
    result = await ingestor.ingest_directory(args.path, dry_run=args.dry_run)
    print(
        "world_observation_ingest "
        f"processed={result.processed_count} "
        f"archived={result.archived_count} "
        f"failed={result.failed_count} "
        f"skipped={result.skipped_count}"
    )
    for file_result in result.results:
        print(f"{file_result.action} {file_result.path}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
