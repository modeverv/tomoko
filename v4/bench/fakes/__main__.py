"""偽のシステムを起こす。

    python -m bench.fakes clockwork --port 8765
"""

import argparse
import asyncio
from pathlib import Path

from bench import definitions as d
from bench.audio import resample
from bench.fakes.server import KINDS, serve
from bench.generate.tts import synthesize

TEXT = ("今日は朝から良い天気ですね。散歩に出かけるにはちょうどいい気温だと思います。"
        "お昼には近くの公園でお弁当を食べるのも良さそうです。夕方からは少し風が出てくるそうなので、"
        "上着を一枚持っていくと安心ですよ。帰りにスーパーに寄って、晩ごはんの材料も買っておきましょうか。")


async def _main(kind: str, port: int, cache: Path) -> None:
    clip = resample(synthesize(TEXT, "Kyoko", cache), d.IN_SR, d.OUT_SR)
    server = await serve(kind, clip, port=port)
    print(f"{kind} ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/ws 声の長さ {len(clip)/d.OUT_SR:.1f} 秒",
          flush=True)
    await server.serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=sorted(KINDS))
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--cache", type=Path, default=Path("work/tts-cache"))
    a = ap.parse_args()
    asyncio.run(_main(a.kind, a.port, a.cache))
