"""V0 の測定に共通する記録と集計。

規則（duplex-handoff.md §3.4）:
- 起点と終点を必ず書く。
- 平均だけでなく p50 / p95 / p99 / 最大を出す。
- 失敗した試行を分母から外さない（attempts と failures を別に持つ）。

百分位は nearest-rank 法（補間しない）。尾側を楽観的に丸めないため。
"""

from __future__ import annotations

import contextlib
import json
import math
import platform
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence


def percentile(values: Sequence[float], p: float) -> float:
    if not values:
        raise ValueError("percentile of empty sequence")
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100.0 * len(ordered)))
    return ordered[rank - 1]


def summarize(samples_ms: Sequence[float], threshold_ms: float | None = None) -> dict[str, Any]:
    n = len(samples_ms)
    out: dict[str, Any] = {"count": n, "threshold_ms": threshold_ms}
    if n == 0:
        for key in ("mean_ms", "min_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms", "stdev_ms"):
            out[key] = None
        return out
    out.update(
        mean_ms=statistics.fmean(samples_ms),
        min_ms=min(samples_ms),
        p50_ms=percentile(samples_ms, 50),
        p95_ms=percentile(samples_ms, 95),
        p99_ms=percentile(samples_ms, 99),
        max_ms=max(samples_ms),
        stdev_ms=statistics.stdev(samples_ms) if n > 1 else 0.0,
    )
    if threshold_ms is not None:
        over = 0
        run = 0
        longest = 0
        long_runs = 0
        for v in samples_ms:
            if v > threshold_ms:
                over += 1
                run += 1
                longest = max(longest, run)
                if run == 3:
                    long_runs += 1
            else:
                run = 0
        out.update(
            over_threshold=over,
            over_threshold_ratio=over / n,
            max_consecutive_over=longest,
            runs_of_3_or_more_over=long_runs,
        )
    return out


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


class LatencyRecorder:
    """1 系列の所要時間を記録する。samples は試行順に保つ（連続超過を数えるため）。"""

    def __init__(
        self,
        name: str,
        start_point: str,
        end_point: str,
        threshold_ms: float | None = None,
        conditions: dict[str, Any] | None = None,
        clock_ns: Callable[[], int] = time.perf_counter_ns,
    ) -> None:
        self.name = name
        self.start_point = start_point
        self.end_point = end_point
        self.threshold_ms = threshold_ms
        self.conditions = dict(conditions or {})
        self.samples_ms: list[float] = []
        self.failures = 0
        self.failure_reasons: Counter[str] = Counter()
        self.extra: dict[str, Any] = {}
        self._clock_ns = clock_ns
        self.started_at: str | None = _now_iso()
        self.finished_at: str | None = None

    def add(self, elapsed_ms: float) -> None:
        self.samples_ms.append(float(elapsed_ms))

    def add_failure(self, reason: str) -> None:
        self.failures += 1
        self.failure_reasons[reason] += 1

    @contextlib.contextmanager
    def time(self) -> Iterator[None]:
        t0 = self._clock_ns()
        try:
            yield
        except BaseException as exc:
            self.add_failure(type(exc).__name__)
            raise
        self.add((self._clock_ns() - t0) / 1e6)

    def to_dict(self) -> dict[str, Any]:
        self.finished_at = _now_iso()
        return {
            "name": self.name,
            "start_point": self.start_point,
            "end_point": self.end_point,
            "conditions": self.conditions,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "host": {"machine": platform.machine(), "platform": platform.platform()},
            "attempts": len(self.samples_ms) + self.failures,
            "failures": self.failures,
            "failure_reasons": dict(self.failure_reasons),
            "summary": summarize(self.samples_ms, self.threshold_ms),
            "extra": self.extra,
            "samples_ms": self.samples_ms,
        }

    def write_json(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1))
        return path


def write_many(path: str | Path, recorders: Sequence[LatencyRecorder], meta: dict[str, Any] | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"meta": meta or {}, "series": [r.to_dict() for r in recorders]}
    path.write_text(json.dumps(body, ensure_ascii=False, indent=1))
    return path


def format_summary(rec: LatencyRecorder) -> str:
    s = summarize(rec.samples_ms, rec.threshold_ms)
    if not s["count"]:
        return f"{rec.name}: no samples (failures={rec.failures})"
    line = (
        f"{rec.name}: n={s['count']} fail={rec.failures} p50={s['p50_ms']:.1f} "
        f"p95={s['p95_ms']:.1f} p99={s['p99_ms']:.1f} max={s['max_ms']:.1f} ms"
    )
    if rec.threshold_ms is not None:
        line += f" over>{rec.threshold_ms:g}={s['over_threshold']} maxrun={s['max_consecutive_over']}"
    return line
