"""V0-2: RTX 3060 に置いた whisper.cpp（whisper-server、large-v3-turbo q5_0）で、発話を書き起こす時間を測る。

M 案では、Gemma に渡すユーザー発話の書き起こし（非同期 STT）を 3060 に置けるかが問い。
起点 → 終点: 発話 1 個分の WAV（16 kHz mono s16）を POST する直前 → 書き起こしの応答を受け取った時刻。
発話が終わってから音声を送る想定なので、この値がそのまま「話し終わり → 書き起こし到着」の下限に近い（VAD の待ちは含まない）。
--with-llm を付けると、同時に llama-server（同じ 3060）へ生成を投げ続け、同居の影響を見る。

  uv run python scripts/v0/stt_remote_probe.py --stt-port 18091 --llm-port 18090 [--with-llm]
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import threading
import time
import urllib.request
import uuid
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
import soundfile as sf

from tomoko_v3.measure import LatencyRecorder, format_summary, write_many

TEXTS = {  # work/audio/u*.wav の元の文（say で合成した）
    1: "こんにちは。今日はいい天気ですね。", 2: "最近、何か面白いことあった？", 3: "晩ごはん、何にしようか迷ってるんだよね。",
    4: "週末はどこかに出かける予定ある？", 5: "昨日ちょっと夜更かししちゃって眠いんだ。", 6: "おすすめの本があったら教えてほしいな。",
    7: "ギターの練習を毎日続けてるんだけど、なかなか上達しないんだよね。", 8: "明日の天気ってどうなるのかな。",
    9: "コーヒーと紅茶だったらどっちが好き？", 10: "最近寒くなってきたね。",
}


def to_wav16k(path: str) -> bytes:
    x, sr = sf.read(path, dtype="float32")
    n = int(len(x) * 16000 / sr)
    y = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def post_inference(port: int, wav: bytes) -> str:
    boundary = uuid.uuid4().hex
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\nContent-Type: audio/wav\r\n\r\n".encode(),
        wav,
        f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\njson\r\n--{boundary}--\r\n".encode(),
    ]
    req = urllib.request.Request(f"http://127.0.0.1:{port}/inference", data=b"".join(parts),
                                 headers={"content-type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["text"]


def llm_load(port: int, stop: threading.Event, counter: list) -> None:
    body = json.dumps({"messages": [{"role": "user", "content": "日本の四季について詳しく説明して。"}],
                       "max_tokens": 300, "stream": False}).encode()
    while not stop.is_set():
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=body,
                                     headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                counter.append(json.loads(r.read()).get("timings", {}).get("predicted_per_second"))
        except Exception:
            counter.append(None)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stt-port", type=int, default=18091)
    ap.add_argument("--llm-port", type=int, default=18090)
    ap.add_argument("--with-llm", action="store_true")
    ap.add_argument("--repeat", type=int, default=3)
    args = ap.parse_args()

    files = sorted(glob.glob("work/audio/u*.wav"), key=lambda p: int(Path(p).stem[1:]))
    wavs = {int(Path(f).stem[1:]): to_wav16k(f) for f in files}
    durations = {k: sf.info(f).duration for k, f in zip(sorted(wavs), files)}
    post_inference(args.stt_port, wavs[1])  # warm-up（集計外）

    stop = threading.Event()
    tps: list = []
    if args.with_llm:
        threading.Thread(target=llm_load, args=(args.llm_port, stop, tps), daemon=True).start()
        time.sleep(2)
    cond = {"server": "whisper.cpp b5454 whisper-server (CUDA 12.4) on RTX 3060", "model": "ggml-large-v3-turbo-q5_0",
            "transport": "ssh tunnel", "with_llm_generation": args.with_llm}
    rec = LatencyRecorder("stt_remote", "WAV POST sent", "transcript received", conditions=cond)
    rows = []
    for _ in range(args.repeat):
        for k, wav in wavs.items():
            t0 = time.perf_counter_ns()
            try:
                text = post_inference(args.stt_port, wav)
            except Exception as exc:
                rec.add_failure(type(exc).__name__)
                continue
            ms = (time.perf_counter_ns() - t0) / 1e6
            rec.add(ms)
            rows.append({"utt": k, "audio_s": round(durations[k], 2), "ms": round(ms, 1), "ref": TEXTS[k], "hyp": text.strip()})
    stop.set()
    print(format_summary(rec))
    for r in rows[:10]:
        print(r["utt"], r["ms"], r["hyp"])
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    out = Path(f"_docs/v0/data/stt-remote-{'withllm' if args.with_llm else 'alone'}-{stamp}.json")
    write_many(out, [rec], meta={**cond, "rows": rows, "llm_tps_during": tps})
    print(out)


if __name__ == "__main__":
    main()
