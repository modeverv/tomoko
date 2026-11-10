"""V0-3: VOICEVOX（v2 の起動手順で 127.0.0.1:50122 に立てたもの）を測る。

VOICEVOX の HTTP API は audio_query → synthesis の 2 段で、synthesis は WAV を一括で返す（逐次ではない）。
起点 → 終点:
- first_audio: audio_query 送信直前 → synthesis 応答本文（WAV）を受け取り終えた時刻（一括なので = 全体）
- query: audio_query 送信直前 → 応答受信
- 位置情報: audio_query の accent_phrases にモーラごとの長さがあるので、文字・モーラの再生時刻を計算できる。
  その合計と実 WAV の長さの差を記録する。

  uv run python scripts/v0/tts_bench_voicevox.py --condition alone --speaker 1
"""

from __future__ import annotations

import argparse
import io
import json
import time
import urllib.parse
import urllib.request
import wave
from datetime import datetime
from pathlib import Path

from tomoko_v3.measure import LatencyRecorder, format_summary, write_many
from tomoko_v3.tts_phrases import PHRASES

BASE = "http://127.0.0.1:50122"


def post(path: str, params: dict, body: bytes | None = None) -> bytes:
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, data=body or b"", method="POST",
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def mora_seconds(q: dict) -> float:
    total = q.get("prePhonemeLength", 0) + q.get("postPhonemeLength", 0)
    for ap in q["accent_phrases"]:
        for m in ap["moras"]:
            total += (m.get("consonant_length") or 0) + m["vowel_length"]
        if ap.get("pause_mora"):
            total += ap["pause_mora"]["vowel_length"]
    return total / q.get("speedScale", 1.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", default="alone")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--speaker", type=int, default=1)
    args = ap.parse_args()

    recs = []
    pos = {}
    post("/audio_query", {"text": "こんにちは", "speaker": args.speaker})  # warm-up（集計外）
    for cls, phrases in PHRASES.items():
        cond = {"backend": "voicevox", "speaker": args.speaker, "class": cls, "condition": args.condition}
        fa = LatencyRecorder(f"voicevox_first_audio_{cls}", "audio_query sent", "synthesis WAV received",
                             threshold_ms=250.0, conditions=cond)
        qq = LatencyRecorder(f"voicevox_query_{cls}", "audio_query sent", "audio_query received", conditions=cond)
        diffs = []
        for _ in range(args.repeat):
            for text in phrases:
                t0 = time.perf_counter_ns()
                try:
                    q = json.loads(post("/audio_query", {"text": text, "speaker": args.speaker}))
                    t1 = time.perf_counter_ns()
                    wav = post("/synthesis", {"speaker": args.speaker}, json.dumps(q).encode())
                except Exception as exc:
                    fa.add_failure(type(exc).__name__)
                    continue
                t2 = time.perf_counter_ns()
                qq.add((t1 - t0) / 1e6)
                fa.add((t2 - t0) / 1e6)
                with wave.open(io.BytesIO(wav)) as w:
                    dur = w.getnframes() / w.getframerate()
                diffs.append(round((dur - mora_seconds(q)) * 1000, 1))
        pos[cls] = {"wav_minus_mora_sum_ms": diffs}
        recs += [fa, qq]
    for r in recs:
        print(format_summary(r))
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    out = Path(f"_docs/v0/data/tts-voicevox-{args.condition}-{stamp}.json")
    write_many(out, recs, meta={"condition": args.condition, "position": pos,
                                "stop_note": "synthesis は一括応答。停止は HTTP 接続を切るしかなく、エンジン側の計算は止まらない（未検証）"})
    print(out)


if __name__ == "__main__":
    main()
