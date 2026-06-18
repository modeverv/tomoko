from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from server.shared.models import utc_now


@dataclass(slots=True)
class JsonlLogger:
    path: Path

    def log(self, event: str, **fields: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": utc_now().isoformat(),
            "event": event,
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
