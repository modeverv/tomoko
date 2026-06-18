from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import Google Calendar iCal feeds.")
    parser.add_argument("--config", default="config/central_realtime.toml")
    parser.add_argument(
        "--urls-file",
        default="config/gcal_urls.txt",
        help="Git-ignored text file with one private iCal URL per line.",
    )
    parser.add_argument("--days-before", type=int, default=1)
    parser.add_argument("--days-ahead", type=int, default=30)
    return parser.parse_args()


async def main() -> None:
    from server.shared.calendar import (
        CalendarIcsImporter,
        PostgresCalendarEventStore,
        read_calendar_urls_file,
    )
    from server.shared.config import NodeConfig

    args = parse_args()
    config = NodeConfig.load(args.config)
    urls = read_calendar_urls_file(Path(args.urls_file))
    if not urls:
        logger.warning("gcal import skipped: no URLs in %s", args.urls_file)
        return

    store = PostgresCalendarEventStore(config.database.dsn)
    await store.ensure_schema()
    importer = CalendarIcsImporter(
        store=store,
        days_before=args.days_before,
        days_ahead=args.days_ahead,
    )
    imported = await importer.import_urls(urls)
    logger.info("gcal import completed feeds=%s events=%s", len(urls), imported)
    print(f"imported calendar events: {imported}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
