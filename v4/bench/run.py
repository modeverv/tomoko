"""/ws につなぎ、入力を実時間の速さで流し、聞こえるはずの音を録る。

    python -m bench.run ws://127.0.0.1:8765/ws work/inputs/small work/runs/名前 --jobs 1

時計はベンチが持つ。入力の最初のサンプルの時刻を 0 とする。
エンジンのコードは読み込まない。
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import numpy as np
import websockets

from bench import definitions as d
from bench import wire
from bench.audio import read_wav, write_wav
from bench.player import Player
from bench.scenario import Manifest

FRAME = int(d.IN_SR * d.FRAME_S)


class _Ears:
    """実行中に、Tomoko が話しているかを音量で見る。採点には使わない（採点は VAD）。"""

    def __init__(self) -> None:
        self.last_loud: float | None = None
        self.run_start: float | None = None

    def update(self, t: float, level: float) -> None:
        if level > d.TOMOKO_RMS:
            if not self.speaking(t):
                self.run_start = t
            self.last_loud = t
        elif not self.speaking(t):
            self.run_start = None

    def speaking(self, t: float) -> bool:
        return self.last_loud is not None and t - self.last_loud <= d.TOMOKO_HANGOVER_S

    def silent_for(self, t: float, since: float) -> float:
        return t - max(since, self.last_loud if self.last_loud is not None else since)


def _due(step, t: float, prev_end: float, ears: _Ears, heard_reply: bool) -> bool:
    if step.at is not None:
        return t >= step.at
    if step.after_tomoko_speaks is not None:
        if not ears.speaking(t) or ears.run_start is None:
            return False
        return t - max(ears.run_start, prev_end) >= step.after_tomoko_speaks
    if t - prev_end >= d.TRIGGER_TIMEOUT_S:
        return True
    if not heard_reply:
        return t - prev_end >= d.RESPONSE_WINDOW_S
    return not ears.speaking(t) and ears.silent_for(t, prev_end) >= step.after_reply


async def run_scenario(url: str, inputs: Path, out: Path) -> dict:
    manifest = Manifest.load(inputs)
    steps = manifest.scenario.steps
    audio = [read_wav(inputs / a.wav)[0] for a in manifest.audio]
    player = Player(d.OUT_SR)
    ears = _Ears()
    events: list[dict] = []
    sent: list[np.ndarray] = []
    fired: list[dict] = []
    record = {
        "id": manifest.scenario.id, "axis": manifest.scenario.axis,
        "definitions": d.VERSION, "private": manifest.private,
        "precondition_failed": False, "error": None,
    }
    loop = asyncio.get_running_loop()
    t = 0.0
    try:
        async with websockets.connect(url, max_size=None) as ws:
            t0 = loop.time() + d.FRAME_S  # フレーム i は、録り終えた時刻 (i+1)*FRAME_S に送る

            async def receive() -> None:
                async for msg in ws:
                    now = loop.time() - t0
                    if isinstance(msg, bytes):
                        gen, seq, samples = wire.unpack_audio(msg)
                        ok = player.on_chunk(now, gen, samples)
                        events.append({"t": round(now, 4), "type": "audio", "generation": gen,
                                       "seq": seq, "n": len(samples), "played": ok})
                    else:
                        obj = json.loads(msg)
                        events.append({"t": round(now, 4)} | obj)
                        if obj.get("type") == "stop":
                            player.on_stop(now, int(obj["generation"]))

            receiver = asyncio.create_task(receive())
            k, active, prev_end, heard_reply = 0, None, 0.0, False
            i = 0
            while True:
                t = i * d.FRAME_S
                ears.update(t, player.level(t))
                heard_reply = heard_reply or (ears.speaking(t) and t > prev_end)
                frame = np.zeros(FRAME, dtype=np.float32)
                if active is None and k < len(steps):
                    if _due(steps[k], t, prev_end, ears, heard_reply):
                        active = (k, i)
                    elif (steps[k].after_tomoko_speaks is not None
                          and t - prev_end >= d.TRIGGER_TIMEOUT_S):
                        record["precondition_failed"] = True
                        break
                if active is not None:
                    idx, i0 = active
                    a = audio[idx]
                    piece = a[(i - i0) * FRAME : (i - i0 + 1) * FRAME]
                    frame[: len(piece)] = piece
                    if (i - i0 + 1) * FRAME >= len(a):
                        start = i0 * d.FRAME_S
                        m = manifest.audio[idx]
                        fired.append({
                            "start": round(start, 4), "end": round(start + m.duration, 4),
                            "voiced": [[round(start + x, 4), round(start + y, 4)] for x, y in m.voiced],
                            "pauses": [[round(start + x, 4), round(start + y, 4)] for x, y in m.pauses],
                        })
                        prev_end, heard_reply = start + m.duration, False
                        active, k = None, k + 1
                elif k >= len(steps):
                    waited = t - prev_end >= d.RESPONSE_WINDOW_S
                    if waited and ears.silent_for(t, prev_end) >= d.TAIL_SILENCE_S:
                        break
                if t >= d.MAX_DURATION_S:
                    break
                sent.append(frame)
                await asyncio.sleep(max(0.0, t0 + t - loop.time()))
                await ws.send(wire.pack_mic(frame))
                if i % 5 == 0:
                    await ws.send(wire.playback_message(*player.played(t)))
                i += 1
            receiver.cancel()
    except (OSError, websockets.WebSocketException) as e:
        record["error"] = f"{type(e).__name__}: {e}"
    record |= {"duration": round(t, 3), "steps": fired, "stale_chunks": player.stale_chunks,
               "events": events}
    out.mkdir(parents=True, exist_ok=True)
    write_wav(out / "input.wav", np.concatenate(sent) if sent else np.zeros(0), d.IN_SR)
    write_wav(out / "output.wav", player.render(t), d.OUT_SR)
    (out / "run.json").write_text(json.dumps(record, ensure_ascii=False))
    return record


async def run_all(url: str, inputs: Path, out: Path, jobs: int) -> list[dict]:
    gate = asyncio.Semaphore(jobs)

    async def one(directory: Path) -> dict:
        async with gate:
            return await run_scenario(url, directory, out / directory.name)

    dirs = sorted(p.parent for p in inputs.glob("*/scenario.json"))
    return await asyncio.gather(*(one(p) for p in dirs))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("inputs", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--jobs", type=int, default=1,
                    help="同時に流す件数。本物のエンジンは 1。偽のシステムの検査では増やしてよい")
    args = ap.parse_args()
    records = asyncio.run(run_all(args.url, args.inputs, args.out, args.jobs))
    errors = [r["id"] for r in records if r["error"]]
    print(f"{len(records)} 件を流した。接続の失敗 {len(errors)} 件 {errors[:5]}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
