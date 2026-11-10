"""VOICEVOX の合成にかかる時間を測る（P3）。単独と、手前の LLM が文を作っている最中とで比べる。

    uv run python scripts/tts_voicevox_bench.py [--url http://127.0.0.1:50021] [--with-llm]

起点 → 終点: /audio_query を送る直前 → /synthesis の応答を受け取り終えた。
"""

import argparse
import asyncio
import statistics
import time

import httpx

from engine.dto import EngineConfig
from engine.front_llm import stream_reply
from engine.speaker.tts_voicevox import VoicevoxTts

PHRASES = ["はい。", "そうですね、", "明日の朝ごはんですね。", "雨の日はゆっくり過ごすのが一番ですよ。",
           "簡単なトーストとか、ヨーグルトにフルーツを乗せるのはどうかな。"]


async def load(client: httpx.AsyncClient, cfg: EngineConfig, stop: asyncio.Event) -> None:
    while not stop.is_set():
        async for _ in stream_reply(client, cfg, [{"role": "user", "content": "旅行の準備について詳しく教えて。"}]):
            if stop.is_set():
                break


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:50021")
    ap.add_argument("--with-llm", action="store_true")
    ap.add_argument("--rounds", type=int, default=5)
    args = ap.parse_args()
    cfg = EngineConfig()
    async with httpx.AsyncClient(timeout=60) as client:
        tts = VoicevoxTts(client, args.url, cfg.voicevox_speaker)
        await tts.warm()
        stop = asyncio.Event()
        task = asyncio.create_task(load(client, cfg, stop)) if args.with_llm else None
        await asyncio.sleep(1.0 if task else 0)
        for phrase in PHRASES:
            times, seconds = [], 0.0
            for _ in range(args.rounds):
                t0 = time.perf_counter()
                audio = await tts.synthesize(phrase)
                times.append(time.perf_counter() - t0)
                seconds = len(audio) / 24_000
            print(f"{len(phrase):3d} 字  音 {seconds:4.2f} 秒  合成 中央値 {statistics.median(times) * 1000:5.0f} ms  "
                  f"最大 {max(times) * 1000:5.0f} ms")
        stop.set()
        if task:
            task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
