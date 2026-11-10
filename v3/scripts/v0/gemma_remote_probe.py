"""V0-2: RTX 3060 の LM Studio 上の Gemma 4 から、thinking なしの返事を得て速さを測る。

chat API はテンプレート側で thinking が既定 on になり止められなかった（duplex-handoff.md §4）。
そこで /v1/completions に Gemma 4 の会話形式を自前で組んで渡す。

起点 → 終点:
- ttft: HTTP リクエスト送信直前 → 最初の非空テキストチャンク受信
- total: 同じ起点 → ストリーム終了
- lan_rtt: TCP 接続の確立時間（ping 相当の下限）

使い方: uv run python scripts/v0/gemma_remote_probe.py --model google/gemma-4-e4b --rounds 4
"""

from __future__ import annotations

import argparse
import json
import socket
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from tomoko_v3.measure import LatencyRecorder, format_summary, write_many

HOST = "192.168.11.46"
PORT = 1234
API = "completions"  # "completions": 会話形式を自前で組む / "chat": サーバーのテンプレートに任せる（llama-server 用）

PROMPTS = [
    "今日はちょっと疲れたよ。",
    "おはよう。よく眠れた？",
    "晩ごはん何にしようかな。",
    "さっき散歩してきたんだ。",
    "最近ギターの練習が楽しいんだよね。",
]

SYSTEM = "あなたは Tomoko。ユーザーと音声で雑談する。返事は話し言葉で、一文か二文で短く答える。"

# 推論が本文に出たかの判定に使う文字列
THINKING_MARKERS = ("<thought>", "Thinking Process", "thought", "<|channel", "思考プロセス", "**Analyze", "1.  **")


def build_prompt(user: str, system: str | None):
    if API == "chat":
        return ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
    sys_part = f"<|turn>system\n{system}<turn|>\n" if system else ""
    return f"{sys_part}<|turn>user\n{user}<turn|>\n<|turn>model\n"


def stream_completion(model: str, prompt: str | list, max_tokens: int) -> dict:
    payload = {"model": model, "max_tokens": max_tokens, "temperature": 0.7, "stream": True,
               "stream_options": {"include_usage": True}}
    if API == "chat":
        payload["messages"] = prompt
        path = "/v1/chat/completions"
    else:
        payload["prompt"] = prompt
        path = "/v1/completions"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(f"http://{HOST}:{PORT}{path}", data=body, headers={"content-type": "application/json"})
    t0 = time.perf_counter_ns()
    ttft = None
    text = []
    chunks = 0
    usage = None
    with urllib.request.urlopen(req, timeout=60) as resp:
        for raw in resp:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            obj = json.loads(data)
            if obj.get("usage"):
                usage = obj["usage"]
            for ch in obj.get("choices", []):
                delta = ch.get("delta") or {}
                if delta.get("reasoning_content"):
                    text.append("<thought>" + delta["reasoning_content"])
                piece = ch.get("text") or delta.get("content") or ""
                if piece:
                    chunks += 1
                    if ttft is None:
                        ttft = (time.perf_counter_ns() - t0) / 1e6
                    text.append(piece)
    total = (time.perf_counter_ns() - t0) / 1e6
    return {"ttft_ms": ttft, "total_ms": total, "text": "".join(text), "chunks": chunks, "usage": usage}


def tcp_rtt_ms() -> float:
    t0 = time.perf_counter_ns()
    with socket.create_connection((HOST, PORT), timeout=5):
        pass
    return (time.perf_counter_ns() - t0) / 1e6


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="google/gemma-4-e4b")
    ap.add_argument("--rounds", type=int, default=4)
    ap.add_argument("--max-tokens", type=int, default=60)
    ap.add_argument("--no-system", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--api", choices=["completions", "chat"], default=API)
    ap.add_argument("--label", default="lmstudio")
    args = ap.parse_args()
    globals().update(HOST=args.host, PORT=args.port, API=args.api)

    system = None if args.no_system else SYSTEM
    cond = {"model": args.model, "server": args.label,
            "endpoint": "/v1/completions (raw Gemma 4 template)" if args.api == "completions" else "/v1/chat/completions", "max_tokens": args.max_tokens,
            "system_prompt": system is not None, "host": f"{HOST}:{PORT}", "warmup_runs_excluded": 1}
    ttft = LatencyRecorder("ttft", "request sent", "first non-empty text chunk", conditions=cond)
    total = LatencyRecorder("total", "request sent", "stream end", conditions=cond)
    tps = LatencyRecorder("chunks_per_s", "first chunk", "stream end (value is chunks/s, not ms)", conditions=cond)
    rtt = LatencyRecorder("tcp_connect", "connect() start", "TCP established", conditions={"host": f"{HOST}:{PORT}"})

    for _ in range(20):
        rtt.add(tcp_rtt_ms())

    stream_completion(args.model, build_prompt(PROMPTS[0], system), 8)  # warmup（集計外）

    runs = []
    for r in range(args.rounds):
        for p in PROMPTS:
            try:
                res = stream_completion(args.model, build_prompt(p, system), args.max_tokens)
            except Exception as exc:  # 失敗も分母に残す
                for rec in (ttft, total):
                    rec.add_failure(type(exc).__name__)
                runs.append({"prompt": p, "error": repr(exc)})
                continue
            thinking = any(m in res["text"] for m in THINKING_MARKERS)
            ok = bool(res["text"].strip()) and not thinking
            if res["ttft_ms"] is None:
                ttft.add_failure("no text")
            else:
                ttft.add(res["ttft_ms"])
            total.add(res["total_ms"])
            if res["ttft_ms"] is not None and res["chunks"] > 1:
                gen_s = (res["total_ms"] - res["ttft_ms"]) / 1000
                if gen_s > 0:
                    tps.add((res["chunks"] - 1) / gen_s)
            runs.append({"round": r, "prompt": p, "ok": ok, "thinking_detected": thinking, **res})
            print(f"[{'OK' if ok else 'NG'}] ttft={res['ttft_ms']} total={res['total_ms']:.0f} {res['text'][:60]!r}")

    n_ok = sum(1 for x in runs if x.get("ok"))
    for rec in (rtt, ttft, total, tps):
        print(format_summary(rec))
    print(f"ok {n_ok}/{len(runs)}")

    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    slug = args.model.split("/")[-1]
    out = Path(args.out or f"_docs/v0/data/gemma-remote-{args.label}-{slug}-{stamp}.json")
    write_many(out, [rtt, ttft, total, tps], meta={"ok": n_ok, "runs_total": len(runs), "runs": runs, **cond})
    print(out)


if __name__ == "__main__":
    main()
