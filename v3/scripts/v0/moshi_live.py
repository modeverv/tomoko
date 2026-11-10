"""V0-2: J-Moshi とヘッドセットで実時間に話す。会話を録音し、tick ごとの所要時間を記録する。

  uv run --group moshi python scripts/v0/moshi_live.py --minutes 10          # q4、既定の入出力装置
  uv run --group moshi python scripts/v0/moshi_live.py --list-devices
  uv run --group moshi python scripts/v0/moshi_live.py -q 8 --in 2 --out 2

Ctrl+C で終わる。終了時に work/live/<時刻>.wav（左: マイク、右: Moshi）と
_docs/v0/data/moshi-live-*.json（tick 時間・締切超過・出力の欠け）を書く。
録音は本人の声を含むので work/（Git 管理外）に置き、この Mac から出さない。
"""

from __future__ import annotations

import argparse
import queue
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

from tomoko_v3.measure import LatencyRecorder, format_summary, write_many
from tomoko_v3.moshi_rt import FRAME, SAMPLE_RATE, MoshiRunner


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-q", "--quantized", type=int, choices=[4, 8], default=4)
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--in", dest="in_dev", default=None)
    ap.add_argument("--out", dest="out_dev", default=None)
    ap.add_argument("--list-devices", action="store_true")
    args = ap.parse_args()
    if args.list_devices:
        print(sd.query_devices())
        return

    def dev(x):
        return int(x) if x is not None and x.isdigit() else x

    max_ticks = int(args.minutes * 60 / 0.08)
    print("loading J-Moshi ...", flush=True)
    runner = MoshiRunner(hf_repo=f"akkikiki/j-moshi-ext-mlx-q{args.quantized}", quantized=args.quantized,
                         max_steps=max_ticks + 10, mimi_backend="mlx")
    in_q: queue.Queue = queue.Queue()
    out_q: queue.Queue = queue.Queue()
    underruns = 0
    started = False

    def on_in(indata, frames, t, status):
        in_q.put(indata[:, 0].astype(np.float32).copy())

    def on_out(outdata, frames, t, status):
        nonlocal underruns
        try:
            outdata[:, 0] = out_q.get_nowait()
        except queue.Empty:
            outdata.fill(0)
            if started:
                underruns += 1

    cond = {"model": f"akkikiki/j-moshi-ext-mlx-q{args.quantized}", "mode": "live headset",
            "in_device": args.in_dev, "out_device": args.out_dev}
    rec = LatencyRecorder("tick_total", "encode_step call", "decode_step return", threshold_ms=80.0, conditions=cond)
    rec_lm = LatencyRecorder("lm_step", "gen.step call", "audio tokens materialized", threshold_ms=80.0, conditions=cond)
    mic, bot, text = [], [], []
    print(f"話してください（最長 {args.minutes} 分、Ctrl+C で終了）", flush=True)
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, blocksize=FRAME, callback=on_in, device=dev(args.in_dev)), \
         sd.OutputStream(samplerate=SAMPLE_RATE, channels=1, blocksize=FRAME, callback=on_out, device=dev(args.out_dev)):
        try:
            for i in range(max_ticks):
                pcm = in_q.get()
                tid, out, tm = runner.tick(pcm)
                started = True
                if i >= 5:
                    rec.add(tm.total_ms)
                    rec_lm.add(tm.lm_ms)
                mic.append(pcm)
                y = out if out is not None else np.zeros(FRAME, dtype=np.float32)
                bot.append(y)
                out_q.put(y)
                piece = runner.piece(tid)
                if piece:
                    text.append(piece)
                    print(piece, end="", flush=True)
        except KeyboardInterrupt:
            pass
    print()
    for r in (rec, rec_lm):
        print(format_summary(r))
    print(f"output underruns: {underruns}, input backlog at end: {in_q.qsize()}")
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    wav = Path(f"work/live/{stamp}-q{args.quantized}.wav")
    wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(wav, np.stack([np.concatenate(mic), np.concatenate(bot)], axis=1), SAMPLE_RATE)
    out = write_many(Path(f"_docs/v0/data/moshi-live-q{args.quantized}-{stamp}.json"), [rec, rec_lm],
                     meta={**cond, "output_underruns": underruns, "recording": str(wav)})
    # 会話の中身は私的な内容を含み得るので、Git 管理外の work/ にだけ置く
    wav.with_suffix(".txt").write_text("".join(text))
    print(wav)
    print(out)


if __name__ == "__main__":
    sys.exit(main())
