"""STT の途中経過を、一定間隔の再認識で作ったときの実測（P2）。

    uv run python scripts/stt_partial_bench.py --model mlx-community/whisper-large-v3-turbo --interval 0.25 [--llm-load]

入力は work/inputs/small の T1 と T2 の発話。発話の先頭から interval 秒ごとに、そこまでの音を認識し直す。

起点 → 終点:
  call_ms       認識の呼び出し直前 → 文字が返った時刻
  間に合った割合  call_ms が interval 以下だった呼び出しの割合（超えると、次の途中経過が遅れる）
  書き換わり      途中経過の文字が、次の途中経過の先頭と一致しなかった割合（句読点と空白を除いて比べる）
  安定までの残り  途中経過が最終結果と同じになった最初の時点から、発話の終わりまでの秒
"""

import argparse
import json
import re
import statistics
import sys
import threading
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import itertools

from bench.audio import read_wav
from engine.dto import EngineConfig
from engine.sensors.stt import WhisperStt

_STRIP = re.compile(r"[\s、。，．,.!?！？「」ー〜~]")


def norm(text: str) -> str:
    return _STRIP.sub("", text)


def llm_load(stop: threading.Event, counter: list[int]) -> None:
    cfg = EngineConfig()
    body = {"model": cfg.llm_model, "max_tokens": 150,
            "messages": [{"role": "user", "content": "東京の観光地を、理由を付けて詳しく教えて。"}],
            "chat_template_kwargs": {"enable_thinking": False}}
    with httpx.Client(timeout=60) as client:
        while not stop.is_set():
            client.post(cfg.llm_url, json=body)
            counter[0] += 1


def pct(values: list[float], q: float) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, round(q * (len(values) - 1)))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="mlx-community/whisper-large-v3-turbo")
    ap.add_argument("--interval", type=float, default=0.25)
    ap.add_argument("--inputs", type=Path, default=Path("work/inputs/small"))
    ap.add_argument("--llm-load", action="store_true", help="手前の LLM に連続で依頼しながら測る")
    ap.add_argument("--out", type=Path, default=Path("_docs/data"))
    args = ap.parse_args()

    stt = WhisperStt(args.model)
    stt.warm()
    wavs = sorted(args.inputs.glob("t1-*/step0.wav")) + sorted(args.inputs.glob("t2-*/step0.wav"))
    stop, counter = threading.Event(), [0]
    if args.llm_load:
        threading.Thread(target=llm_load, args=(stop, counter), daemon=True).start()
        time.sleep(1.0)

    calls, rewrites, pairs, settle, finals = [], 0, 0, [], []
    for wav in wavs:
        audio, sr = read_wav(wav)
        step = round(args.interval * sr)
        texts = []
        for end in [*range(step, len(audio), step), len(audio)]:
            t0 = time.perf_counter()
            texts.append((end / sr, stt.transcribe(audio[:end])))
            calls.append({"audio_s": end / sr, "call_ms": (time.perf_counter() - t0) * 1000})
        final = norm(texts[-1][1])
        finals.append(calls[-1]["call_ms"])
        for (_, a), (_, b) in itertools.pairwise(texts):
            pairs += 1
            rewrites += not norm(b).startswith(norm(a))
        first_same = next(t for t, text in texts if norm(text) == final)
        settle.append(len(audio) / sr - first_same)
    stop.set()

    ms = [c["call_ms"] for c in calls]
    summary = {
        "model": args.model, "interval_s": args.interval, "llm_load": args.llm_load,
        "llm_requests_during": counter[0], "utterances": len(wavs), "calls": len(calls),
        "call_ms_p50": round(pct(ms, 0.5), 1), "call_ms_p95": round(pct(ms, 0.95), 1),
        "call_ms_max": round(max(ms), 1),
        "final_call_ms_p50": round(pct(finals, 0.5), 1), "final_call_ms_p95": round(pct(finals, 0.95), 1),
        "kept_up_share": round(sum(m <= args.interval * 1000 for m in ms) / len(ms), 3),
        "rewrite_share": round(rewrites / pairs, 3),
        "settled_before_end_s_p50": round(statistics.median(settle), 2),
        "settled_only_at_end_share": round(sum(s == 0 for s in settle) / len(settle), 3),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    tag = f"{args.model.split('/')[-1]}-{args.interval}{'-llm' if args.llm_load else ''}"
    (args.out / f"stt-partial-{tag}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
