"""V0-2: J-Moshi（MLX）の 1 tick の所要時間を測る。

1 tick = 80 ms の入力音声 → Mimi encode → LM 1 step（本体 + depformer）→ Mimi decode。
入力は合成音声の発話と無音を交互に並べたものを、80 ms ごとの実時間ペースで流す
（--free を付けると待たずに連続で回す）。

起点 → 終点: tick 開始（encode_step 呼び出し直前）→ decode_step の戻り。
締切: 80 ms。負荷条件（--condition）は記録用のラベルで、負荷そのものは別プロセスで掛ける。

例: uv run --group moshi python scripts/v0/moshi_tick_bench.py -q 4 --seconds 300 --condition alone
"""

from __future__ import annotations

import argparse
import glob
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import soundfile as sf

from tomoko_v3.measure import LatencyRecorder, format_summary, write_many
from tomoko_v3.moshi_rt import FRAME, MoshiRunner


def build_input(seconds: float, audio_glob: str, gap_s: float = 4.0) -> np.ndarray:
    files = sorted(glob.glob(audio_glob))
    if not files:
        raise SystemExit(f"no input audio: {audio_glob}")
    parts = []
    total = 0
    need = int(seconds * 24000)
    i = 0
    while total < need:
        x, sr = sf.read(files[i % len(files)], dtype="float32")
        assert sr == 24000, sr
        parts += [x, np.zeros(int(gap_s * 24000), dtype=np.float32)]
        total += len(x) + int(gap_s * 24000)
        i += 1
    return np.concatenate(parts)[:need]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-q", "--quantized", type=int, choices=[4, 8], default=4)
    ap.add_argument("--seconds", type=float, default=300)
    ap.add_argument("--condition", default="alone")
    ap.add_argument("--free", action="store_true", help="実時間ペースにせず連続で回す")
    ap.add_argument("--audio", default="work/audio/u*.wav")
    ap.add_argument("--save-wav", default=None)
    ap.add_argument("--mimi", choices=["rust", "mlx"], default="rust")
    args = ap.parse_args()

    repo = f"akkikiki/j-moshi-ext-mlx-q{args.quantized}"
    t_load = time.perf_counter()
    runner = MoshiRunner(hf_repo=repo, quantized=args.quantized, max_steps=int(args.seconds / 0.08) + 10,
                         mimi_backend=args.mimi)
    load_s = time.perf_counter() - t_load

    pcm = build_input(args.seconds, args.audio)
    n = len(pcm) // FRAME
    cond = {"model": repo, "quantized": args.quantized, "condition": args.condition,
            "paced_realtime": not args.free, "mimi_backend": args.mimi, "ticks": n, "input": args.audio}
    rec_total = LatencyRecorder("tick_total", "encode_step call", "decode_step return", threshold_ms=80.0, conditions=cond)
    rec_enc = LatencyRecorder("mimi_encode", "encode_step call", "encode_step return", conditions=cond)
    rec_lm = LatencyRecorder("lm_step", "gen.step call", "audio tokens materialized", threshold_ms=80.0, conditions=cond)
    rec_dec = LatencyRecorder("mimi_decode", "decode_step call", "decode_step return", conditions=cond)
    rec_late = LatencyRecorder("tick_finish_lateness", "tick deadline (start + 80 ms * i)", "tick finished (positive = late)", conditions=cond)

    outs = []
    texts = []
    t_start = time.perf_counter()
    first_ms = []
    for i in range(n):
        if not args.free:
            target = t_start + i * 0.08
            delay = target - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
        text_id, out, tm = runner.tick(pcm[i * FRAME:(i + 1) * FRAME])
        if i < 5:
            first_ms.append(tm.total_ms)
        else:  # 最初の 5 tick は warm-up として別に記録
            rec_total.add(tm.total_ms)
            rec_enc.add(tm.encode_ms)
            rec_lm.add(tm.lm_ms)
            rec_dec.add(tm.decode_ms)
            rec_late.add((time.perf_counter() - (t_start + (i + 1) * 0.08)) * 1000)
        if out is not None:
            outs.append(out)
        texts.append(runner.piece(text_id))

    for r in (rec_total, rec_enc, rec_lm, rec_dec, rec_late):
        print(format_summary(r))
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    out = Path(f"_docs/v0/data/moshi-tick-q{args.quantized}-{args.mimi}-{args.condition}-{stamp}.json")
    import mlx.core as mx
    meta = {**cond, "load_s": load_s, "first5_ticks_ms": first_ms,
            "peak_memory_gb": mx.get_peak_memory() / 1e9 if hasattr(mx, "get_peak_memory") else None,
            "moshi_text": "".join(texts)[:4000]}
    write_many(out, [rec_total, rec_enc, rec_lm, rec_dec, rec_late], meta=meta)
    print(out)
    if args.save_wav and outs:
        mono_out = np.concatenate(outs)
        m = min(len(mono_out), len(pcm))
        sf.write(args.save_wav, np.stack([pcm[:m], mono_out[:m]], axis=1), 24000)
        print(args.save_wav)


if __name__ == "__main__":
    main()
