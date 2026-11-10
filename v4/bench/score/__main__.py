"""実行の結果を採点する。使うのは、入力の注釈（run.json の steps）と output.wav だけ。

    python -m bench.score work/runs/名前 [--content --inputs work/inputs/small]
"""

import argparse
import json
import sys
from pathlib import Path

from bench import definitions as d
from bench.scenario import Manifest
from bench.score.content import score_content
from bench.score.segments import speech_segments
from bench.score.timing import StepTime, score_timing


def score_run(directory: Path, inputs: Path | None, content: bool) -> dict:
    run = json.loads((directory / "run.json").read_text())
    out = {k: run[k] for k in ("id", "axis", "error", "precondition_failed", "stale_chunks")}
    if run["error"]:
        return out
    segs = speech_segments(directory / "output.wav")
    steps = [StepTime(s["start"], s["end"], tuple(map(tuple, s["voiced"])),
                      tuple(map(tuple, s["pauses"]))) for s in run["steps"]]
    out["segments"] = [[round(a, 3), round(b, 3)] for a, b in segs]
    out["timing"] = score_timing(run["axis"], steps, segs, run["precondition_failed"])
    if content and run["axis"] in ("T1", "T4", "C2", "C3"):
        sc = Manifest.load(inputs / run["id"]).scenario
        out["content"] = score_content(run, [s.text() for s in sc.steps], sc.expect, segs,
                                       directory / "output.wav")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", type=Path)
    ap.add_argument("--content", action="store_true", help="中身も採点する（書き起こしと採点役を使う）")
    ap.add_argument("--inputs", type=Path, help="--content のときに要る。台本と期待する答えを読む")
    args = ap.parse_args()
    if args.content and not args.inputs:
        ap.error("--content には --inputs が要る")
    dirs = sorted(p.parent for p in args.run.glob("*/run.json"))
    runs = [score_run(p, args.inputs, args.content) for p in dirs]
    (args.run / "results.json").write_text(
        json.dumps({"definitions": d.VERSION, "runs": runs}, ensure_ascii=False, indent=1))
    print(f"{len(runs)} 件を採点した: {args.run / 'results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
