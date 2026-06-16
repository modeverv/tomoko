from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def _main() -> int:
    from server.thinker2.main import async_main

    return await async_main()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
