from __future__ import annotations

import asyncio

from server.runtime import run_process

if __name__ == "__main__":
    asyncio.run(run_process("tomoko"))
