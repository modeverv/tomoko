"""V0-3: Apple の音声合成を (1) 常駐 sidecar と (2) say コマンド（毎回プロセス起動）で測る。

起点 → 終点（すべて Python 側の time.perf_counter_ns）:
- first_audio: 句のテキストを sidecar の stdin に書いた直前 → 最初の音声バッファの行を受け取った時刻
- total: 同じ起点 → done の行を受け取った時刻
- stop: 停止の行を書いた直前 → stopped の行を受け取った時刻。その後 300 ms 以内に届いた同じ句のバッファ数も数える
- say: say プロセス起動直前 → プロセス終了（ファイル出力。逐次ではないので最初の音 = 全体）

  uv run python scripts/v0/tts_bench_apple.py --condition alone --repeat 3
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import tempfile
import threading
import time
import queue
from datetime import datetime
from pathlib import Path

from tomoko_v3.measure import LatencyRecorder, format_summary, write_many
from tomoko_v3.tts_phrases import PHRASES

SIDECAR = "work/apple_tts"
VOICE = "com.apple.voice.compact.ja-JP.Kyoko"


class Sidecar:
    def __init__(self) -> None:
        self.p = subprocess.Popen([SIDECAR], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.q: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.p.stdout:
            self.q.put((time.perf_counter_ns(), json.loads(line)))

    def send(self, obj) -> int:
        t = time.perf_counter_ns()
        self.p.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.p.stdin.flush()
        return t

    def get(self, timeout=10.0):
        return self.q.get(timeout=timeout)

    def drain(self, seconds: float) -> list:
        end = time.time() + seconds
        out = []
        while time.time() < end:
            try:
                out.append(self.q.get(timeout=max(0.0, end - time.time())))
            except queue.Empty:
                break
        return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", default="alone")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--voice", default=VOICE)
    args = ap.parse_args()

    sc = Sidecar()
    sc.send({"voices": True})
    voices = sc.get()[1]
    recs = []
    marker_info = {}
    # 起動直後の 1 回目は cold として別に記録する
    t0 = sc.send({"id": "cold", "text": "こんにちは", "voice": args.voice})
    cold = None
    while True:
        t, ev = sc.get()
        if ev.get("event") == "buffer" and cold is None:
            cold = (t - t0) / 1e6
        if ev.get("event") == "done":
            break
    for cls, phrases in PHRASES.items():
        cond = {"backend": "apple-sidecar", "voice": args.voice, "class": cls, "condition": args.condition}
        fa = LatencyRecorder(f"apple_first_audio_{cls}", "text written to sidecar stdin", "first buffer line received",
                             threshold_ms=250.0, conditions=cond)
        tot = LatencyRecorder(f"apple_total_{cls}", "text written to sidecar stdin", "done line received", conditions=cond)
        words = []
        for r in range(args.repeat):
            for k, text in enumerate(phrases):
                rid = f"{cls}-{r}-{k}"
                t0 = sc.send({"id": rid, "text": text, "voice": args.voice})
                first = None
                n_word = 0
                try:
                    while True:
                        t, ev = sc.get()
                        if ev.get("id") != rid:
                            continue
                        if ev["event"] == "buffer" and first is None:
                            first = (t - t0) / 1e6
                        if ev["event"] == "marker" and ev["kind"] == "word":
                            n_word += 1
                        if ev["event"] == "done":
                            tot.add((t - t0) / 1e6)
                            break
                except queue.Empty:
                    fa.add_failure("timeout")
                    tot.add_failure("timeout")
                    continue
                if first is None:
                    fa.add_failure("no audio")
                else:
                    fa.add(first)
                words.append(n_word)
        marker_info[cls] = {"word_markers_per_phrase": words}
        recs += [fa, tot]

    # 停止の反応: 20 モーラの句を流し、最初のバッファを受けたら止める
    stop = LatencyRecorder("apple_stop", "stop line written", "stopped line received", conditions={"backend": "apple-sidecar"})
    leaked = []
    for k, text in enumerate(PHRASES["m20"] * args.repeat):
        rid = f"stop-{k}"
        sc.send({"id": rid, "text": text + "。" + text, "voice": args.voice})
        while True:
            t, ev = sc.get()
            if ev.get("id") == rid and ev["event"] == "buffer":
                break
        ts = sc.send({"stop": True})
        stopped_at = None
        after = 0
        for t, ev in sc.drain(0.3):
            if ev.get("event") == "stopped" and stopped_at is None:
                stopped_at = t
                stop.add((t - ts) / 1e6)
            elif ev.get("id") == rid and ev.get("event") == "buffer" and stopped_at is not None:
                after += 1
        if stopped_at is None:
            stop.add_failure("no stopped line")
        leaked.append(after)
        sc.drain(0.5)  # 残りを捨てる
    stop.extra["buffers_after_stopped_within_300ms"] = leaked
    recs.append(stop)

    # say コマンド（毎回プロセス起動、ファイル出力）
    for cls in ("m05", "m20"):
        rec = LatencyRecorder(f"say_process_{cls}", "subprocess spawn", "process exit (file written)",
                              threshold_ms=250.0, conditions={"backend": "say", "voice": "Kyoko", "class": cls,
                                                              "condition": args.condition})
        with tempfile.TemporaryDirectory() as d:
            for r in range(args.repeat):
                for k, text in enumerate(PHRASES[cls]):
                    t0 = time.perf_counter_ns()
                    res = subprocess.run(["say", "-v", "Kyoko", "-o", f"{d}/x.aiff", text])
                    if res.returncode != 0:
                        rec.add_failure(f"rc={res.returncode}")
                    else:
                        rec.add((time.perf_counter_ns() - t0) / 1e6)
        recs.append(rec)

    for r in recs:
        print(format_summary(r))
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    out = Path(f"_docs/v0/data/tts-apple-{args.condition}-{stamp}.json")
    write_many(out, recs, meta={"condition": args.condition, "voices": voices, "cold_first_audio_ms": cold,
                                "markers": marker_info})
    print(out)
    sc.p.stdin.close()


if __name__ == "__main__":
    main()
