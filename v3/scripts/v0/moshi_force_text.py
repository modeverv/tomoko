"""V0-2: テキスト列の上書き（学習なし）。Moshi に外から与えた日本語の文を話させられるか。

手順（1 試行）: 発話音声を実時間相当で流す → 発話が終わった tick で置き換え列を仕込む →
その後 --tail 秒の無音を流す。Moshi のテキスト列のうち「語」を出そうとした tick を置き換える。

方式:
  a: 出だしから置き換える（話し出すタイミングは Moshi、中身は外の文）
  b: Moshi 自身の語を --skip token 通してから置き換える（途中から置き換え）
  c: 発話終了直後から毎 tick 連続で置き換える（タイミングも外が決める。比較用）
  p: Moshi が最初の語を出そうとした tick から、Moshi の形（語の前に EPAD、長さに応じた PAD）で外から並べる

出力: work/force/<mode>-<i>.wav（左: 入力、右: Moshi）と _docs/v0/data/moshi-force-*.json
起点 → 終点（記録する時間）: 入力発話の最後のサンプル → Moshi のテキスト列に最初の置き換え token が出た tick
"""

from __future__ import annotations

import argparse
import glob
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import soundfile as sf

from tomoko_v3.moshi_rt import FRAME, SILENT_TEXT, MoshiRunner

# u1..u10 に対する返事（Gemma が書いた想定の文。作成者: Claude Code）
REPLIES = [
    "ほんとだね、空がすごく青くて気持ちいいよ。",
    "うん、昨日新しいパン屋さんを見つけたんだ。",
    "寒いから、あったかいお鍋なんてどうかな。",
    "まだ決めてないけど、公園を散歩したいな。",
    "それは眠いよね、今日は早めに寝ようね。",
    "最近読んだ推理小説がすごく面白かったよ。",
    "毎日続けてるのがえらいよ、きっと上達してるって。",
    "明日は晴れて、少し暖かくなるみたいだよ。",
    "私はコーヒーかな、朝に飲むのが好きなんだ。",
    "ほんとに寒いね、風邪ひかないように気をつけてね。",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-q", "--quantized", type=int, choices=[4, 8], default=4)
    ap.add_argument("--modes", default="a,b,c")
    ap.add_argument("--skip", type=int, default=3)
    ap.add_argument("--tail", type=float, default=8.0)
    ap.add_argument("--lead", type=float, default=1.0, help="発話の前に流す無音（秒）")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--seed", type=int, default=299792458)
    ap.add_argument("--tpc", type=float, default=1.6, help="p 方式の 1 文字あたり tick 数")
    args = ap.parse_args()

    files = sorted(glob.glob("work/audio/u*.wav"), key=lambda p: int(Path(p).stem[1:]))[: args.n]
    outdir = Path("work/force")
    outdir.mkdir(parents=True, exist_ok=True)
    trials = []
    for mode in args.modes.split(","):
        for i, f in enumerate(files):
            # 試行ごとに状態（KV cache）を作り直す。モデル読み込みは数秒なので許容する
            runner = MoshiRunner(hf_repo=f"akkikiki/j-moshi-ext-mlx-q{args.quantized}", quantized=args.quantized,
                                 forcing=mode != "none", mimi_backend="mlx", seed=args.seed + i, max_steps=400)
            x, sr = sf.read(f, dtype="float32")
            lead = np.zeros(int(args.lead * sr), dtype=np.float32)
            tail = np.zeros(int(args.tail * sr), dtype=np.float32)
            pcm = np.concatenate([lead, x, tail])
            speech_end_tick = (len(lead) + len(x)) // FRAME
            target = REPLIES[i]
            target_ids = runner.encode_text(target)
            n = len(pcm) // FRAME
            outs, emitted_text, model_text = [], [], []
            first_forced_tick = None
            for t in range(n):
                if t == speech_end_tick and mode != "none":
                    if mode == "a":
                        runner.text_sampler.push(target_ids, mode="words", skip_words=0, after="pad")
                    elif mode == "b":
                        runner.text_sampler.push(target_ids, mode="words", skip_words=args.skip, after="pad")
                    elif mode == "p":
                        runner.text_sampler.push(runner.paced_tokens(target, args.tpc), mode="paced", after="pad")
                    elif mode == "c":
                        runner.text_sampler.push(target_ids, mode="contiguous", after="pad")
                tid, out, _ = runner.tick(pcm[t * FRAME:(t + 1) * FRAME])
                if out is not None:
                    outs.append(out)
                emitted_text.append(runner.piece(tid))
                if mode != "none":
                    _, sampled, emitted = runner.text_sampler.state.log[-1]
                    model_text.append(runner.piece(sampled))
                    if first_forced_tick is None and runner.text_sampler.state.armed and emitted not in SILENT_TEXT and t >= speech_end_tick:
                        first_forced_tick = t
            y = np.concatenate(outs) if outs else np.zeros(0, dtype=np.float32)
            m = min(len(y), len(pcm))
            wav = outdir / f"{mode}-{i + 1:02d}.wav"
            sf.write(wav, np.stack([pcm[:m], y[:m]], axis=1), sr)
            sf.write(outdir / f"{mode}-{i + 1:02d}-moshi.wav", y, sr)
            leftover = len(runner.text_sampler.state.queue) if mode != "none" else None
            trial = {
                "mode": mode, "index": i + 1, "input": f, "target": target, "target_tokens": len(target_ids),
                "speech_end_tick": speech_end_tick,
                "first_forced_tick": first_forced_tick,
                "speech_end_to_first_forced_ms": None if first_forced_tick is None
                else (first_forced_tick - speech_end_tick) * 80,
                "unconsumed_forced_tokens": leftover,
                "text_stream": "".join(emitted_text),
                "model_wanted_text": "".join(model_text),
                "wav": str(wav),
            }
            trials.append(trial)
            print(json.dumps({k: trial[k] for k in ("mode", "index", "speech_end_to_first_forced_ms",
                                                     "unconsumed_forced_tokens", "text_stream")}, ensure_ascii=False))
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    out = Path(f"_docs/v0/data/moshi-force-q{args.quantized}-{stamp}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"args": vars(args), "trials": trials}, ensure_ascii=False, indent=1))
    print(out)


if __name__ == "__main__":
    main()
