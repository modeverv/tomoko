"""V0: 別プロセスで LLM に生成を続けさせる負荷。tick 測定の「LLM 同時実行」条件に使う。

mlx-lm は moshi_mlx と mlx の版が合わないので、別環境（uvx）で動かす:
  uvx --python 3.12 --from mlx-lm python scripts/v0/load_llm.py --model mlx-community/gemma-4-E4B-it-4bit --seconds 330

生成の速さ（tok/s）を 1 回ごとに JSON Lines で --log に書く（負荷の大きさの記録）。
"""

import argparse
import json
import time

from mlx_lm import load, stream_generate

PROMPT = "日本の四季と、それぞれの季節の行事や食べ物について、できるだけ詳しく説明してください。"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="mlx-community/gemma-4-E4B-it-4bit")
    ap.add_argument("--seconds", type=float, default=330)
    ap.add_argument("--max-tokens", type=int, default=256)
    ap.add_argument("--log", default=None)
    args = ap.parse_args()

    model, tok = load(args.model)
    prompt = tok.apply_chat_template([{"role": "user", "content": PROMPT}], add_generation_prompt=True, tokenize=False)
    end = time.time() + args.seconds
    log = open(args.log, "a") if args.log else None
    print("load ready", flush=True)
    while time.time() < end:
        t0 = time.time()
        n = 0
        last = None
        for last in stream_generate(model, tok, prompt, max_tokens=args.max_tokens):
            n += 1
            if time.time() >= end:
                break
        rec = {"t": t0, "tokens": n, "wall_s": time.time() - t0,
               "gen_tps": getattr(last, "generation_tps", None), "prompt_tps": getattr(last, "prompt_tps", None)}
        if log:
            log.write(json.dumps(rec) + "\n")
            log.flush()


if __name__ == "__main__":
    main()
