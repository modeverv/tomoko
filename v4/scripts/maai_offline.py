"""MaAI の vap が、言い終わりと言いよどみを分けられるかを、録った入力で調べる（P3）。エンジンは使わない。

    uv run python scripts/maai_offline.py work/runs/baseline-1 --out _docs/data/maai-offline.json

ch1 にユーザーの入力（input.wav）、ch2 に無音を入れて vap を回す。入力の中の無音（0.2 秒以上）を拾い、
  shift  発話の終わり（台本の step の終わり）
  hold   発話の途中（台本の言いよどみ、合成の声が文の間で黙ったところ）
に分ける。無音が始まってから d 秒後の p_now / p_future（ch2 = Tomoko が話す見込み）を比べる。
"""

import argparse
import itertools
import json
import queue
import statistics
from pathlib import Path

import numpy as np

from bench.audio import read_wav
from bench.score.segments import speech_segments

FRAME = 160
DELAYS = (0.1, 0.2, 0.3, 0.4)


def run_vap(audio: np.ndarray, lang: str, frame_rate: int) -> list[tuple[float, float, float]]:
    """(入力の時刻, p_now[ch2], p_future[ch2]) の列を返す。"""
    from maai import Maai, MaaiInput

    ch1, ch2 = MaaiInput.Chunk(), MaaiInput.Chunk()
    vap = Maai(mode="vap", lang=lang, frame_rate=frame_rate, context_len_sec=5,
               audio_ch1=ch1, audio_ch2=ch2, device="cpu")
    vap.start()
    out, zero, fed = [], np.zeros(FRAME, dtype=np.float32), 0
    per_result = round(16_000 / frame_rate / FRAME)
    try:
        for i in range(0, len(audio) - FRAME + 1, FRAME):
            ch1.put_chunk(audio[i : i + FRAME])
            ch2.put_chunk(zero)
            fed += 1
            if fed % per_result:
                continue
            try:  # MaAI は入力が 100 個たまると捨てる。結果を 1 つ受け取ってから次を入れる
                r = vap.result_dict_queue.get(timeout=5.0)
            except queue.Empty:
                continue
            out.append((fed * FRAME / 16_000, float(r["p_now"][1]), float(r["p_future"][1])))
    finally:
        vap.stop(wait=False)
    return out


def at(series: list[tuple[float, float, float]], t: float) -> tuple[float, float] | None:
    past = [s for s in series if s[0] <= t]
    return (past[-1][1], past[-1][2]) if past else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--lang", default="jp")
    ap.add_argument("--frame-rate", type=int, default=10)
    ap.add_argument("--axes", default="T1,T2,C2,T5")
    args = ap.parse_args()
    rows = []
    for d in sorted(p for p in args.run.iterdir() if (p / "run.json").exists()):
        run = json.loads((d / "run.json").read_text())
        if run["axis"] not in args.axes.split(","):
            continue
        audio, _ = read_wav(d / "input.wav")
        series = run_vap(audio.astype(np.float32), args.lang, args.frame_rate)
        voiced = speech_segments(d / "input.wav")
        for step in run["steps"]:
            inside = [s for s in voiced if s[1] > step["start"] - 0.1 and s[0] < step["end"] + 0.1]
            for a, b in itertools.pairwise(inside):
                if b[0] - a[1] >= 0.2:
                    rows.append({"id": run["id"], "kind": "hold", "t": a[1], "gap": b[0] - a[1]})
            if inside:
                rows.append({"id": run["id"], "kind": "shift", "t": inside[-1][1], "gap": None})
        for row in rows:
            if row["id"] == run["id"]:
                for delay in DELAYS:
                    row[f"d{delay}"] = at(series, row["t"] + delay)
        print(d.name, len(series), "結果", flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    for delay in DELAYS:
        for which, name in ((0, "p_now"), (1, "p_future")):
            line = f"無音から {delay} 秒後の {name}: "
            for kind in ("shift", "hold"):
                v = [r[f"d{delay}"][which] for r in rows if r["kind"] == kind and r.get(f"d{delay}")]
                # 言いよどみは、その時点でまだ無音が続いているものだけを比べる
                if kind == "hold":
                    v = [r[f"d{delay}"][which] for r in rows
                         if r["kind"] == kind and r.get(f"d{delay}") and r["gap"] > delay]
                if v:
                    q = statistics.quantiles(v, n=10)
                    line += f"{kind} {len(v)} 件 10%点 {q[0]:.2f} 中央値 {statistics.median(v):.2f} 90%点 {q[-1]:.2f}  "
            print(line)


if __name__ == "__main__":
    main()
