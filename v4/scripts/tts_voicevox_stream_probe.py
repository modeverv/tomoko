"""VOICEVOX の逐次合成（/streaming_synthesis、PR 1823 の版）を確かめる（P3）。

    uv run python scripts/tts_voicevox_stream_probe.py --url http://127.0.0.1:50122 [--out work/vv-stream]

配布版にはない口なので、PR 1823 を入れたエンジンが要る。句ごとに、ふつうの合成（/synthesis）と比べる。
起点 → 終点: /audio_query を送る直前 → 音の最初の断片を受け取った（最初の音）/ 全部を受け取った（完了）。
"""

import argparse
import asyncio
import statistics
import time
from pathlib import Path

import httpx
import numpy as np

from bench.audio import write_wav

PHRASES = ["はい。", "そうですね、", "明日の朝ごはんですね。", "雨の日はゆっくり過ごすのが一番ですよ。",
           "簡単なトーストとか、ヨーグルトにフルーツを乗せるのはどうかな。"]
HEADER = 44


async def one(client: httpx.AsyncClient, url: str, speaker: int, text: str, path: str,
              params: dict) -> tuple[float, float, np.ndarray]:
    t0 = time.perf_counter()
    r = await client.post(f"{url}/audio_query", params={"text": text, "speaker": speaker})
    r.raise_for_status()
    query = r.json() | {"outputSamplingRate": 24_000, "outputStereo": False}
    first, data = None, b""
    async with client.stream("POST", f"{url}/{path}", params={"speaker": speaker, **params},
                             json=query) as response:
        response.raise_for_status()
        async for part in response.aiter_bytes():
            data += part
            if first is None and len(data) > HEADER:
                first = time.perf_counter() - t0
    pcm = np.frombuffer(data[HEADER : HEADER + (len(data) - HEADER) // 2 * 2], dtype="<i2")
    return first, time.perf_counter() - t0, (pcm / 32768).astype(np.float32)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:50122")
    ap.add_argument("--speaker", type=int, default=8)
    ap.add_argument("--segment", type=float, default=0.3)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    async with httpx.AsyncClient(timeout=120) as client:
        await client.post(f"{args.url}/initialize_speaker", params={"speaker": args.speaker})
        await one(client, args.url, args.speaker, "こんにちは。", "synthesis", {})
        for n, text in enumerate(PHRASES):
            for path, params in (("synthesis", {}), ("streaming_synthesis", {"segment_length": args.segment})):
                firsts, dones, audio = [], [], np.zeros(0)
                for _ in range(args.rounds):
                    first, done, audio = await one(client, args.url, args.speaker, text, path, params)
                    firsts.append(first)
                    dones.append(done)
                print(f"{len(text):3d} 字 {path:20s} 最初の音 {statistics.median(firsts) * 1000:5.0f} ms  "
                      f"完了 {statistics.median(dones) * 1000:5.0f} ms  音 {len(audio) / 24_000:4.2f} 秒")
                if args.out:
                    args.out.mkdir(parents=True, exist_ok=True)
                    write_wav(args.out / f"{n}-{path}.wav", audio, 24_000)


if __name__ == "__main__":
    asyncio.run(main())
