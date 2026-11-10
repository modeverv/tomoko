"""採点の結果を軸ごとにまとめ、前回との差を出す。

    python -m bench.report work/runs/名前 --previous work/runs/前回
"""

import argparse
import json
import statistics
import sys
from pathlib import Path

LABELS = {
    "n": "件数", "silent": "無音の回", "errors": "接続の失敗", "precondition_failed": "前提不成立",
    "valid": "前提が成り立った件数",
    "response_rate": "応答率", "early_rate": "早すぎた割合",
    "talked_over_rate": "かぶせ続けた割合", "latency_p50": "遅れ 中央値(秒)",
    "latency_p90": "遅れ 90%点(秒)", "interruption_rate": "割り込み率", "stop_rate": "2 秒以内に止まった割合",
    "stop_latency_p50": "停止の遅れ 中央値(秒)", "per_minute": "相づち 回/分",
    "in_gap_share": "間に入った割合", "overlapped_share": "発話に被った割合", "bursts": "連発の回数",
    "main_interruptions": "本発話で遮った回数", "double_answers": "二重の答え", "stale_chunks": "停止後に届いた古い断片",
    "score_mean": "関連性 平均(0〜2)", "judged": "採点した件数", "accuracy": "正答率",
    "first_reaction_p50": "最初の受け答え 中央値(秒)", "answer_time_p50": "答えまで 中央値(秒)",
    "longest_silence_p50": "黙り込んだ最長 中央値(秒)",
}


def _rate(flags: list) -> float | None:
    return sum(bool(f) for f in flags) / len(flags) if flags else None


def _pct(values: list[float], q: float) -> float | None:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    return values[min(len(values) - 1, round(q * (len(values) - 1)))]


def _turn(ts: list[dict]) -> dict:
    lat = [t["latency"] for t in ts]
    return {
        "response_rate": _rate([t["responded"] for t in ts]),
        "early_rate": _rate([t["early"] for t in ts]),
        "talked_over_rate": _rate([t["talked_over"] for t in ts]),
        "latency_p50": _pct(lat, 0.5), "latency_p90": _pct(lat, 0.9),
        "double_answers": sum(t["double_answer"] for t in ts),
    }


def summarize(runs: list[dict]) -> dict[str, dict]:
    """軸ごとの数字。答えなかった回は分母に残す。前提不成立の回は別に数える。"""
    out: dict[str, dict] = {}
    for axis in sorted({r["axis"] for r in runs}):
        rs = [r for r in runs if r["axis"] == axis]
        ok = [r for r in rs if not r["error"]]
        ts = [r["timing"] for r in ok]
        s: dict = {
            "n": len(rs), "errors": len(rs) - len(ok),
            "silent": sum(t["silent"] for t in ts),
            "precondition_failed": sum(r["precondition_failed"] for r in ok),
        }
        if axis in ("T1", "C2", "C3"):
            s |= _turn(ts)
        elif axis == "T2":
            s |= {"interruption_rate": _rate([t["interrupted"] for t in ts]),
                  "response_rate": _rate([t["responded"] for t in ts])}
        elif axis in ("T3", "T4"):
            valid = [t for t in ts if t["precondition"]]
            s["precondition_failed"] = len(ts) - len(valid)
            s["valid"] = len(valid)
            if axis == "T3":
                s["interruption_rate"] = _rate([t["interrupted"] for t in valid])
            else:
                s |= {"stop_rate": _rate([t["stopped"] for t in valid]),
                      "stop_latency_p50": _pct([t["stop_latency"] for t in valid], 0.5),
                      "response_rate": _rate([t["responded"] for t in valid]),
                      "latency_p50": _pct([t["latency"] for t in valid], 0.5)}
        elif axis == "T5":
            count = sum(t["count"] for t in ts)
            s |= {"per_minute": statistics.mean(t["per_minute"] for t in ts) if ts else None,
                  "in_gap_share": sum(t["in_gap"] for t in ts) / count if count else None,
                  "overlapped_share": sum(t["overlapped"] for t in ts) / count if count else None,
                  "bursts": sum(t["bursts"] for t in ts),
                  "main_interruptions": sum(t["main_interruptions"] for t in ts)}
        out[axis] = s

    judged = [r for r in runs if r.get("content", {}).get("judged") in ("ok", "no_reply")]
    c1 = [r["content"] for r in judged if r["axis"] in ("T1", "T4")]
    if c1:
        out["C1"] = {"judged": len(c1), "score_mean": statistics.mean(c["score"] for c in c1)}
    for axis in ("C2", "C3"):
        cs = [r["content"] for r in judged if r["axis"] == axis]
        if not cs:
            continue
        out[axis] |= {"judged": len(cs), "accuracy": _rate([c["correct"] for c in cs])}
        if axis == "C3":
            out[axis] |= {f"{k}_p50": _pct([c.get(k) for c in cs], 0.5)
                          for k in ("first_reaction", "answer_time", "longest_silence")}
    out["R1"] = {"stale_chunks": sum(r.get("stale_chunks", 0) for r in runs),
                 "double_answers": sum(s.get("double_answers", 0) for s in out.values())}
    return out


def _fmt(v) -> str:
    if v is None:
        return "—"
    return f"{v:.3f}" if isinstance(v, float) else str(v)


def render(summary: dict, previous: dict | None = None, title: str = "") -> str:
    lines = [f"# {title}".rstrip(), "", "| 軸 | 指標 | 今回 | 前回 | 差 |", "|---|---|---|---|---|"]
    for axis, metrics in summary.items():
        for key, value in metrics.items():
            old = (previous or {}).get(axis, {}).get(key)
            both = isinstance(value, (int, float)) and isinstance(old, (int, float))
            diff = f"{value - old:+.3f}" if both and value != old else ""
            lines.append(f"| {axis} | {LABELS.get(key, key)} | {_fmt(value)} | "
                         f"{_fmt(old) if previous else ''} | {diff} |")
    return "\n".join(lines) + "\n"


def load(run_dir: Path) -> dict:
    return json.loads((run_dir / "results.json").read_text())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", type=Path)
    ap.add_argument("--previous", type=Path)
    args = ap.parse_args()
    now = load(args.run)
    prev = None
    if args.previous:
        old = load(args.previous)
        if old["definitions"] != now["definitions"]:
            print(f"定義の版が違うので比べない: {old['definitions']} と {now['definitions']}")
            return 2
        prev = summarize(old["runs"])
    text = render(summarize(now["runs"]), prev, f"{args.run.name}（定義 v{now['definitions']}）")
    (args.run / "report.md").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
