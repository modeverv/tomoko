"""V0-4（T 案）: Mimi のエンコーダーを 80 ms 分の音声ごとに 1 回ずつ回し、1 回の時間を測る。
量子化前の連続特徴（512 次元 / 12.5 Hz）を、MLX 版 Mimi の encode_step の中身を分けて取り出す。

起点 → 終点: 80 ms の PCM を渡す直前 → 連続特徴（と、比較用に離散コード）を mx.eval し終えた時刻。
  uv run --group moshi python scripts/v0/mimi_encoder_bench.py --seconds 300
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import mlx.core as mx
import numpy as np
from huggingface_hub import hf_hub_download
from moshi_mlx import models

sys.path.insert(0, str(Path(__file__).resolve().parent))
from moshi_tick_bench import build_input  # noqa: E402

from tomoko_v3.measure import LatencyRecorder, format_summary, write_many
from tomoko_v3.moshi_rt import FRAME


def encode_latent_step(mimi, pcm: mx.array) -> mx.array:
    """`Mimi.encode_step` から quantizer.encode を除いたもの（moshi_mlx 0.3.0 の実装に合わせる）。"""
    xs = mimi.encoder.step(pcm)
    xs = mimi.encoder_transformer(xs, cache=mimi.encoder_cache)[0]
    xs = mimi.downsample.step(xs)
    return xs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=300)
    ap.add_argument("--condition", default="alone")
    ap.add_argument("--with-codes", action="store_true", help="離散コードまで出す（Moshi と同じ）")
    args = ap.parse_args()

    f = hf_hub_download("akkikiki/j-moshi-ext-mlx-q4", "tokenizer-e351c8d8-checkpoint125.safetensors")
    mimi = models.mimi.Mimi(models.mimi_202407(32))
    mimi.load_pytorch_weights(f, strict=True)
    mimi.warmup()
    mimi.reset_all()
    pcm = build_input(args.seconds, "work/audio/u*.wav")
    n = len(pcm) // FRAME
    cond = {"encoder": "Mimi (MLX, moshi_mlx 0.3.0)", "output": "codes" if args.with_codes else "pre-quantizer latent",
            "condition": args.condition, "ticks": n, "paced_realtime": True}
    rec = LatencyRecorder("mimi_encoder_tick", "80ms PCM handed", "output evaluated", threshold_ms=80.0, conditions=cond)
    shape = None
    t_start = time.perf_counter()
    for i in range(n):
        delay = t_start + i * 0.08 - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        x = mx.array(pcm[i * FRAME:(i + 1) * FRAME])[None, None, :]
        ts = time.perf_counter_ns()
        lat = encode_latent_step(mimi, x)
        out = mimi.quantizer.encode(lat) if args.with_codes else lat
        mx.eval(out)
        ms = (time.perf_counter_ns() - ts) / 1e6
        if i >= 5:
            rec.add(ms)
        if shape is None:
            shape = list(lat.shape)
    print(format_summary(rec))
    print("latent shape per tick:", shape)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    out = Path(f"_docs/v0/data/mimi-encoder-{'codes' if args.with_codes else 'latent'}-{args.condition}-{stamp}.json")
    write_many(out, [rec], meta={**cond, "latent_shape_per_tick": shape,
                                 "how": "encoder.step -> encoder_transformer -> downsample.step（quantizer.encode の直前）"})
    print(out)


if __name__ == "__main__":
    main()
