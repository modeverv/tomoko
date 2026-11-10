"""再生の規則。ブラウザと同じ規則を、時刻を引数に取る形で書く。

- 届いた断片は、前の断片の続きに置く。前の断片が終わっていれば、届いた時刻に置く。
- 停止（世代 g）が来たら、g 以前の世代の音を、その時刻より先で捨てる。
  以後に届く g 以前の断片も捨てる。
- 判断はしない。
"""

from dataclasses import dataclass

import numpy as np

from bench.audio import rms


@dataclass
class _Placed:
    start: int  # タイムライン上のサンプル位置
    generation: int
    samples: np.ndarray


class Player:
    def __init__(self, sample_rate: int) -> None:
        self.sr = sample_rate
        self._placed: list[_Placed] = []
        self._cursor = 0
        self._stopped = -1
        self.stale_chunks = 0  # 停止済みの世代なのに届いた断片（サーバー側の不変条件の違反）

    def on_chunk(self, now: float, generation: int, samples: np.ndarray) -> bool:
        if generation <= self._stopped:
            self.stale_chunks += 1
            return False
        start = max(round(now * self.sr), self._cursor)
        self._placed.append(_Placed(start, generation, np.asarray(samples, dtype=np.float32)))
        self._cursor = start + len(samples)
        return True

    def on_stop(self, now: float, generation: int) -> None:
        self._stopped = max(self._stopped, generation)
        cut = round(now * self.sr)
        kept: list[_Placed] = []
        for p in self._placed:
            if p.generation <= generation and p.start + len(p.samples) > cut:
                if p.start >= cut:
                    continue
                p = _Placed(p.start, p.generation, p.samples[: cut - p.start])
            kept.append(p)
        self._placed = kept
        self._cursor = max((p.start + len(p.samples) for p in kept), default=0)

    def render(self, duration: float | None = None) -> np.ndarray:
        end = max((p.start + len(p.samples) for p in self._placed), default=0)
        n = max(end, round((duration or 0.0) * self.sr))
        out = np.zeros(n, dtype=np.float32)
        for p in self._placed:
            out[p.start : p.start + len(p.samples)] += p.samples
        return out

    def level(self, now: float, window: float = 0.1) -> float:
        """now の直前 window 秒に鳴っている音の大きさ。"""
        hi = round(now * self.sr)
        lo = max(0, hi - int(window * self.sr))
        buf = np.zeros(hi - lo, dtype=np.float32)
        for p in reversed(self._placed):
            a, b = max(p.start, lo), min(p.start + len(p.samples), hi)
            if a < b:
                buf[a - lo : b - lo] += p.samples[a - p.start : b - p.start]
            if p.start + len(p.samples) < lo:
                break
        return rms(buf)

    def played(self, now: float) -> tuple[int | None, float]:
        """いちばん新しく鳴り始めた世代と、その世代を何秒再生したか。"""
        hi = round(now * self.sr)
        started = [p for p in self._placed if p.start < hi]
        if not started:
            return None, 0.0
        gen = started[-1].generation
        n = sum(min(p.start + len(p.samples), hi) - p.start for p in started if p.generation == gen)
        return gen, n / self.sr
