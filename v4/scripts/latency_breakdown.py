"""T1 の遅れを、部品ごとに分ける（P2）。エンジンのトレースと、ベンチの実行記録を突き合わせる。

    uv run python scripts/latency_breakdown.py work/runs/baseline-1 --traces work/traces --last 165 --index 0

トレースは接続ごとに 1 つ、作られた順に並ぶ。ベンチは台本を id の順に 1 件ずつ流すので、順番で対応させる。
--last は「最後の何本のトレースがこの一連の実行か」、--index は「そのうち何回目の実行か」。

区間（どれも、入力の時計での秒）:
  vad      ユーザーが言い終わった → VAD が言い終わりと判定した
  stt      書き起こしを始めた → 文字が返った
  llm      LLM に頼んだ → 最初の文字
  phrase   最初の文字 → 最初の句がそろって TTS に渡した
  tts      TTS に渡した → 最初の句の声ができた
  deliver  声ができた → ベンチに最初の断片が届いた
  onset    最初の断片が届いた → 採点の VAD が声の頭を拾った（声の頭の無音と、VAD の遅れ）
"""

import argparse
import json
import statistics
from pathlib import Path

STAGES = ("vad", "stt", "llm", "phrase", "tts", "deliver", "onset")


def load_trace(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.open()]


def breakdown(run: dict, result: dict, trace: list[dict]) -> dict | None:
    end = run["steps"][-1]["end"]
    onset = next((s[0] for s in result["segments"] if s[1] - s[0] >= 1.0 and s[0] >= end - 0.2), None)
    ends = [e for e in trace if e["event"] == "vad_end"]
    if onset is None or not ends:
        return None
    vad_end = min(ends, key=lambda e: abs(e["speech_end_s"] - end))
    after = [e for e in trace if e["t"] >= vad_end["t"]]

    def first(name: str, **match) -> dict | None:
        return next((e for e in after if e["event"] == name
                     and all(e.get(k) == v for k, v in match.items())), None)

    stt0, stt1 = first("stt_start"), first("stt_final")
    llm0, llm1 = first("llm_start"), first("llm_first_token")
    tts0, tts1 = first("tts_start", phrase=0), first("tts_done", phrase=0)
    if not all((stt0, stt1, llm0, llm1, tts0, tts1)):
        return None
    arrived = next((e["t"] for e in run["events"]
                    if e.get("type") == "audio" and e["t"] >= tts1["audio_s"] - 0.05), None)
    if arrived is None:
        return None
    return {
        "vad": vad_end["audio_s"] - end, "stt": stt1["t"] - stt0["t"], "llm": llm1["t"] - llm0["t"],
        "phrase": tts0["t"] - llm1["t"], "tts": tts1["t"] - tts0["t"],
        "deliver": arrived - tts1["audio_s"], "onset": onset - arrived, "total": onset - end,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", type=Path)
    ap.add_argument("--traces", type=Path, default=Path("work/traces"))
    ap.add_argument("--last", type=int, required=True)
    ap.add_argument("--index", type=int, default=0)
    ap.add_argument("--axis", default="T1")
    args = ap.parse_args()
    results = json.loads((args.run / "results.json").read_text())["runs"]
    traces = sorted(args.traces.glob("*.jsonl"))[-args.last:]
    traces = traces[args.index * len(results) : (args.index + 1) * len(results)]
    rows = []
    for result, trace in zip(sorted(results, key=lambda r: r["id"]), traces):
        if result["axis"] != args.axis:
            continue
        run = json.loads((args.run / result["id"] / "run.json").read_text())
        row = breakdown(run, result, load_trace(trace))
        if row:
            rows.append(row)
    print(f"{args.run.name} {args.axis}: {len(rows)} 件")
    for key in (*STAGES, "total"):
        values = [r[key] for r in rows]
        print(f"  {key:8s} 中央値 {statistics.median(values) * 1000:6.0f} ms   "
              f"最小 {min(values) * 1000:6.0f}   最大 {max(values) * 1000:6.0f}")


if __name__ == "__main__":
    main()
