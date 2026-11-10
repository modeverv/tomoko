"""V0-5（T 案）: LLM を「1 tick に 1 位置だけ進める」使い方で回し、tick 時間を測る。学習前なので中身は問わない。

手順: 約 500 トークンの prompt で KV cache を作る → 80 ms ごとに 1 トークンを足して次トークンの分布を計算 → --ticks 回。
--plan-every tick に 1 回、その tick の中で 12 トークンを連続生成する（句を書く動作の模擬）。
入力は通常のトークンで代用する（埋め込みを直接渡す場合と計算量は同じ。duplex-handoff.md §6 V0-5）。

起点 → 終点: tick 開始（入力トークンを渡す直前）→ logits を mx.eval し終えた時刻（句の tick は 12 トークン目まで）。
締切は 80 ms。句の tick が締切を超えたとき、後続の tick が何 tick 遅れたか（lateness）も記録する。

mlx の版が moshi_mlx と合わないので別環境で動かす:
  uvx --python 3.12 --from mlx-lm python scripts/v0/llm_tick_bench.py --model mlx-community/gemma-4-e2b-it-4bit --condition alone
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import mlx.core as mx
from mlx_lm import load
from mlx_lm.models.cache import make_prompt_cache

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tomoko_v3.measure import LatencyRecorder, format_summary, write_many  # noqa: E402

FILLER = ("あなたは Tomoko。ユーザーと音声で雑談する相手。話し言葉で短く答える。"
          "相手が話している間は黙って聞き、話し終わったら返事をする。相槌は短く。") * 20


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="mlx-community/gemma-4-e2b-it-4bit")
    ap.add_argument("--ticks", type=int, default=3750)
    ap.add_argument("--prompt-tokens", type=int, default=500)
    ap.add_argument("--plan-every", type=int, default=20)
    ap.add_argument("--plan-tokens", type=int, default=12)
    ap.add_argument("--condition", default="alone")
    ap.add_argument("--free", action="store_true")
    args = ap.parse_args()

    t_load = time.perf_counter()
    model, tok = load(args.model)
    load_s = time.perf_counter() - t_load
    ids = tok.encode(FILLER)[: args.prompt_tokens]
    cache = make_prompt_cache(model)
    t0 = time.perf_counter_ns()
    logits = model(mx.array(ids)[None], cache=cache)
    mx.eval(logits)
    prefill_ms = (time.perf_counter_ns() - t0) / 1e6

    cond = {"model": args.model, "condition": args.condition, "prompt_tokens": len(ids),
            "plan_every": args.plan_every, "plan_tokens": args.plan_tokens, "paced_realtime": not args.free}
    normal = LatencyRecorder("llm_tick", "token fed", "logits evaluated", threshold_ms=80.0, conditions=cond)
    plan = LatencyRecorder("llm_plan_tick", "token fed", f"{args.plan_tokens}th generated token evaluated",
                           threshold_ms=80.0, conditions=cond)
    late = LatencyRecorder("tick_lateness", "tick deadline", "tick finished (positive = late)", conditions=cond)
    by_ctx: dict[int, list[float]] = {}

    nxt = int(mx.argmax(logits[0, -1]).item())
    t_start = time.perf_counter()
    for i in range(args.ticks):
        if not args.free:
            delay = t_start + i * 0.08 - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
        is_plan = args.plan_every > 0 and i % args.plan_every == args.plan_every - 1
        ts = time.perf_counter_ns()
        logits = model(mx.array([[nxt]]), cache=cache)
        mx.eval(logits)
        nxt = int(mx.argmax(logits[0, -1]).item())
        if is_plan:
            for _ in range(args.plan_tokens - 1):
                logits = model(mx.array([[nxt]]), cache=cache)
                nxt = int(mx.argmax(logits[0, -1]).item())
        ms = (time.perf_counter_ns() - ts) / 1e6
        (plan if is_plan else normal).add(ms)
        late.add((time.perf_counter() - (t_start + (i + 1) * 0.08)) * 1000)
        ctx = cache[0].offset if hasattr(cache[0], "offset") else len(ids) + i
        if not is_plan:
            by_ctx.setdefault(int(ctx) // 500 * 500, []).append(ms)

    ctx_table = {k: {"n": len(v), "p50": sorted(v)[len(v) // 2], "p99": sorted(v)[int(len(v) * 0.99) - 1 if len(v) > 1 else 0]}
                 for k, v in sorted(by_ctx.items())}
    for r in (normal, plan, late):
        print(format_summary(r))
    print("ctx bins:", {k: round(v["p50"], 1) for k, v in ctx_table.items()})
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    slug = args.model.split("/")[-1]
    out = Path(f"_docs/v0/data/llm-tick-{slug}-{args.condition}-{stamp}.json")
    write_many(out, [normal, plan, late], meta={**cond, "load_s": load_s, "prefill_ms": prefill_ms,
                                                 "by_context_len": ctx_table,
                                                 "peak_memory_gb": mx.get_peak_memory() / 1e9})
    print(out)


if __name__ == "__main__":
    main()
