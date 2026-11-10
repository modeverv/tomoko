"""全イベントを、時刻付きで JSONL に書く。原因調べに使う。採点には使わない。"""

import json
import time
from pathlib import Path


class Trace:
    def __init__(self, path: Path | None) -> None:
        self._t0 = time.perf_counter()
        self._file = None
        self.audio_s = 0.0  # 受け取った入力の長さ。ベンチの時計と同じ進み方をする
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = path.open("w")

    def event(self, name: str, **fields) -> None:
        if self._file is None:
            return
        row = {"t": round(time.perf_counter() - self._t0, 4), "audio_s": round(self.audio_s, 4),
               "event": name} | fields
        self._file.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._file.flush()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
