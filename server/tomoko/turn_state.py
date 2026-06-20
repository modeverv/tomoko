from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from server.shared.models import TurnMaterials


@dataclass(slots=True)
class TurnMaterialState:
    latest: TurnMaterials | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def update(self, materials: TurnMaterials) -> None:
        async with self._lock:
            self.latest = materials

    async def get_latest(self) -> TurnMaterials | None:
        async with self._lock:
            return self.latest
