"""予測済みの列だけを使う MaAI の集計。モデル・torch・ネットワークは使わない。"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from statistics import mean

import numpy as np

from bench.maai_input import OfflineClip

DELAYS = (0.1, 0.2, 0.3, 0.4)
BC_REGIONS = ("pause_comma", "pause_period", "pause_other", "speech", "end")
SCORES = {
    "vap": ("p_now", "p_future"),
    "bc": ("p_bc",),
    "bc_2type": ("p_bc_react", "p_bc_emo"),
    "bc_det": ("p_bc_det",),
}


def auc(positive: list[float], negative: list[float]) -> float | None:
    """P(positive > negative)。同点は 0.5、片方が空なら未定義。"""
    if not positive or not negative:
        return None
    return sum((p > n) + 0.5 * (p == n) for p in positive for n in negative) / (
        len(positive) * len(negative)
    )


def distribution(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": None, "p10": None, "median": None, "p90": None}
    p10, median, p90 = (float(x) for x in np.quantile(values, (0.1, 0.5, 0.9)))
    return {"n": len(values), "mean": mean(values), "p10": p10, "median": median, "p90": p90}


def _in(t: float, spans: list[tuple[float, float]]) -> bool:
    return any(a - 1e-9 <= t < b - 1e-9 for a, b in spans)


def _region(clip: OfflineClip, t: float) -> str | None:
    for event in clip.events:
        if event["kind"] == "hold" and _in(t, [(event["t"], event["t"] + event["gap"])]):
            return f"pause_{event['pause_type']}"
    if _in(t, clip.voiced):
        return "speech"
    if any(e["kind"] == "shift" and _in(t, [(e["t"], e["t"] + 0.4)]) for e in clip.events):
        return "end"
    return None


def evaluate(clip: OfflineClip, mode: str, frames: list[dict]) -> list[dict]:
    """時刻は音声先頭から。区間は [start, end)、vap は未来の予測を参照しない。"""
    identity = {"id": clip.id, "axis": clip.axis, "step": clip.step}
    if mode == "vap":
        times = [f["t"] for f in frames]
        rows = []
        for event in clip.events:
            row = identity | event
            for delay in DELAYS:
                index = bisect_right(times, event["t"] + delay + 1e-9) - 1
                observed = index >= 0 and event["t"] + delay <= times[-1] + 1e-9
                row[f"d{delay}"] = [frames[index][k] for k in SCORES[mode]] if observed else None
            rows.append(row)
        return rows
    if mode in {"bc", "bc_2type"}:
        if clip.axis != "T5":
            return []
        return [
            identity | frame | {"region": region}
            for frame in frames
            if (region := _region(clip, frame["t"])) is not None
        ]
    if mode != "bc_det":
        raise ValueError(f"未知の mode: {mode}")
    if not clip.detection_voiced:
        return []
    end = clip.detection_voiced[-1][1]
    selected = [
        f for f in frames if _in(f["t"], clip.detection_voiced) or _in(f["t"], [(end, end + 0.4)])
    ]
    values = [f["p_bc_det"] for f in selected]
    return [
        identity
        | {
            "kind": "backchannel" if clip.axis == "T3" else "interrupt",
            "max": max(values) if values else None,
            "distribution": distribution(values),
            "frames": selected,
        }
    ]


def _vap_summary(rows: list[dict]) -> list[dict]:
    out = []
    for axis in sorted({r["axis"] for r in rows}):
        for delay in DELAYS:
            for which, score in enumerate(SCORES["vap"]):
                values = defaultdict(list)
                for row in rows:
                    if row["axis"] != axis or row.get(f"d{delay}") is None:
                        continue
                    if row["kind"] == "hold" and row["gap"] <= delay:
                        continue
                    values[row["kind"]].append(row[f"d{delay}"][which])
                out.append(
                    {
                        "axis": axis,
                        "delay": delay,
                        "score": score,
                        "auc": auc(values["shift"], values["hold"]),
                        "shift": distribution(values["shift"]),
                        "hold": distribution(values["hold"]),
                    }
                )
    return out


def summarize(results: dict[str, list[dict]]) -> dict:
    out = {}
    for mode, rows in results.items():
        if mode == "vap":
            out[mode] = _vap_summary(rows)
        elif mode in {"bc", "bc_2type"}:
            out[mode] = [
                {
                    "region": region,
                    "score": score,
                    "distribution": distribution([r[score] for r in rows if r["region"] == region]),
                }
                for score in SCORES[mode]
                for region in BC_REGIONS
            ]
        elif mode == "bc_det":
            values = {
                kind: [r["max"] for r in rows if r["kind"] == kind and r["max"] is not None]
                for kind in ("backchannel", "interrupt")
            }
            out[mode] = {
                "auc": auc(values["backchannel"], values["interrupt"]),
                "backchannel": distribution(values["backchannel"]),
                "interrupt": distribution(values["interrupt"]),
                "score": "max over final voiced intervals and 0.4 s after end",
                "frame_distributions": {
                    kind: distribution(
                        [f["p_bc_det"] for r in rows if r["kind"] == kind for f in r["frames"]]
                    )
                    for kind in values
                },
            }
    return out
