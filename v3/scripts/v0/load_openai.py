"""V0: OpenAI 互換サーバー（v2 の dflash 26B など）に生成を投げ続ける負荷。tick 測定の「26B 同時」条件に使う。

  uv run python scripts/v0/load_openai.py --port 8082 --model gemma-4-26b-a4b-it-mlx --seconds 330 --log x.jsonl
"""

import argparse
import json
import time
import urllib.request

PROMPT = "日本の四季と、それぞれの季節の行事や食べ物について、できるだけ詳しく説明してください。"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8082)
    ap.add_argument("--model", default="gemma-4-26b-a4b-it-mlx")
    ap.add_argument("--seconds", type=float, default=330)
    ap.add_argument("--max-tokens", type=int, default=256)
    ap.add_argument("--log", default=None)
    args = ap.parse_args()
    body = json.dumps({"model": args.model, "messages": [{"role": "user", "content": PROMPT}],
                       "max_tokens": args.max_tokens, "stream": True}).encode()
    end = time.time() + args.seconds
    log = open(args.log, "a") if args.log else None
    while time.time() < end:
        t0 = time.time()
        chunks = 0
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{args.port}/v1/chat/completions", data=body,
                                         headers={"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=300) as r:
                for line in r:
                    if line.startswith(b"data:") and b"[DONE]" not in line:
                        chunks += 1
                    if time.time() >= end:
                        break
            err = None
        except Exception as exc:
            err = repr(exc)
            time.sleep(1)
        if log:
            log.write(json.dumps({"t": t0, "chunks": chunks, "wall_s": time.time() - t0, "error": err}) + "\n")
            log.flush()


if __name__ == "__main__":
    main()
