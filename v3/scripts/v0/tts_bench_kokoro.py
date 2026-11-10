"""V0-3: Kokoro（kokoro-mlx、日本語音声 jf_alpha）の最初の音・全体・停止を測る。

v1 の `server/shared/inference/tts/kokoro_mlx.py` と同じく、日本語の音素化を pyopenjtalk に差し替える。
mlx の版が moshi_mlx と合わないので別環境で動かす:
  uvx --python 3.12 --with kokoro-mlx --with 'misaki[ja]' --with numpy python scripts/v0/tts_bench_kokoro.py --condition alone

起点 → 終点:
- first_audio: generate_stream() 呼び出し直前 → 最初のチャンク（numpy）を受け取った時刻
- total: 同じ起点 → イテレーター終了
- stop: 最初のチャンクの後に生成を打ち切った時刻 → 次のチャンクを待たずに戻れた時刻（同期イテレーターなので、
  進行中のチャンク計算は中断できない。打ち切りは「次を取りに行かない」ことでしかできない点を記録する）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tomoko_v3.measure import LatencyRecorder, format_summary, write_many  # noqa: E402
from tomoko_v3.tts_phrases import PHRASES  # noqa: E402


def install_ja(tts) -> None:
    from kokoro_mlx.phonemize import Phonemizer
    from misaki import ja

    ph = object.__new__(Phonemizer)
    ph._vocab = tts._config.vocab
    ph._language = "ja"
    ph._g2p = ja.JAG2P(version="pyopenjtalk")
    tts._phonemizers["ja"] = ph


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", default="alone")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--voice", default="jf_alpha")
    args = ap.parse_args()

    from kokoro_mlx import KokoroTTS

    t_load = time.perf_counter()
    tts = KokoroTTS.from_pretrained()
    install_ja(tts)
    load_s = time.perf_counter() - t_load

    def stream(text):
        return tts.generate_stream(text, voice=args.voice, speed=1.0, sample_rate=24000, language="ja")

    t0 = time.perf_counter_ns()
    next(iter(stream("こんにちは")))
    cold = (time.perf_counter_ns() - t0) / 1e6

    recs = []
    chunks_info = {}
    for cls, phrases in PHRASES.items():
        cond = {"backend": "kokoro-mlx", "voice": args.voice, "class": cls, "condition": args.condition}
        fa = LatencyRecorder(f"kokoro_first_audio_{cls}", "generate_stream call", "first chunk received",
                             threshold_ms=250.0, conditions=cond)
        tot = LatencyRecorder(f"kokoro_total_{cls}", "generate_stream call", "iterator exhausted", conditions=cond)
        n_chunks = []
        for _ in range(args.repeat):
            for text in phrases:
                t0 = time.perf_counter_ns()
                first = None
                n = 0
                try:
                    for chunk in stream(text):
                        if first is None:
                            first = (time.perf_counter_ns() - t0) / 1e6
                        n += 1
                except Exception as exc:  # 失敗も分母に残す
                    fa.add_failure(type(exc).__name__)
                    tot.add_failure(type(exc).__name__)
                    continue
                tot.add((time.perf_counter_ns() - t0) / 1e6)
                if first is None:
                    fa.add_failure("no audio")
                else:
                    fa.add(first)
                n_chunks.append(n)
        chunks_info[cls] = n_chunks
        recs += [fa, tot]

    stop = LatencyRecorder("kokoro_stop", "decide to stop after first chunk", "returned without next chunk",
                           conditions={"backend": "kokoro-mlx"})
    for text in PHRASES["m20"] * args.repeat:
        it = iter(stream(text + "。" + text))
        next(it)
        ts = time.perf_counter_ns()
        it.close() if hasattr(it, "close") else None
        stop.add((time.perf_counter_ns() - ts) / 1e6)
    stop.extra["note"] = "同期イテレーター。進行中のチャンク計算は止められず、次を取らないことでしか止まらない"
    recs.append(stop)

    for r in recs:
        print(format_summary(r))
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    out = Path(f"_docs/v0/data/tts-kokoro-{args.condition}-{stamp}.json")
    write_many(out, recs, meta={"condition": args.condition, "load_s": load_s, "cold_first_audio_ms": cold,
                                "chunks_per_phrase": chunks_info,
                                "position_info": "kokoro-mlx の generate_stream は音声チャンクのみ返す（単語位置なし）"})
    print(out)
    print(json.dumps({"cold_ms": cold}))


if __name__ == "__main__":
    main()
