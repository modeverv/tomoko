"""答えの分かっている偽のシステム。採点器が正しいかを確かめるために使う。

  silent      何も返さない
  chatterbox  つないだ瞬間から話し続ける
  clockwork   入力が 0.5 秒黙ったら、2 秒話す。話している間は聞かない
  polite      入力が 0.5 秒黙ったら、長く話す。話している間に相手が 0.3 秒話したら止まり、
              相手が黙って 0.8 秒後にまた話す

言い終わりは音量だけで見る（入力の注釈と同じ閾値）。賢いことはしない。
"""

import asyncio

import numpy as np
import websockets

from bench import definitions as d
from bench import wire

LOUD = 0.005  # bench.audio.trim_silence と同じ閾値
CHUNK = int(d.OUT_SR * 0.08)


class _Voice:
    """1 接続ぶんの、声の送り手。"""

    def __init__(self, ws) -> None:
        self.ws = ws
        self.generation = -1
        self.task: asyncio.Task | None = None

    def busy(self) -> bool:
        return self.task is not None and not self.task.done()

    def say(self, clip: np.ndarray, loop_forever: bool = False) -> None:
        self.generation += 1
        self.task = asyncio.create_task(self._send(self.generation, clip, loop_forever))

    async def _send(self, generation: int, clip: np.ndarray, loop_forever: bool) -> None:
        loop = asyncio.get_running_loop()
        t0, seq, pos = loop.time(), 0, 0
        try:
            while loop_forever or pos < len(clip):
                piece = np.take(clip, range(pos, pos + CHUNK), mode="wrap") if loop_forever \
                    else clip[pos : pos + CHUNK]
                await self.ws.send(wire.pack_audio(generation, seq, piece))
                seq, pos = seq + 1, pos + CHUNK
                # 2 断片ぶん先に送る。本物の TTS も実時間より速く作り、再生側に余裕を持たせる
                await asyncio.sleep(max(0.0, t0 + (seq - 2) * CHUNK / d.OUT_SR - loop.time()))
        except websockets.ConnectionClosed:
            pass

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
        await self.ws.send(wire.stop_message(self.generation))


async def _mic(ws):
    """届いたマイクの音を、(フレーム, それが loud か) で返す。"""
    async for msg in ws:
        if isinstance(msg, bytes):
            frame = wire.unpack_mic(msg)
            yield frame, bool(len(frame)) and float(np.max(np.abs(frame))) > LOUD


async def silent(ws, clip: np.ndarray) -> None:
    async for _ in ws:
        pass


async def chatterbox(ws, clip: np.ndarray) -> None:
    voice = _Voice(ws)
    voice.say(clip, loop_forever=True)
    async for _ in ws:
        pass


async def clockwork(ws, clip: np.ndarray) -> None:
    voice = _Voice(ws)
    heard, quiet = False, 0.0
    async for frame, loud in _mic(ws):
        if voice.busy():
            heard, quiet = False, 0.0
            continue
        if loud:
            heard, quiet = True, 0.0
        elif heard:
            quiet += len(frame) / d.IN_SR
            if quiet >= 0.5 - 1e-9:
                voice.say(clip[: 2 * d.OUT_SR])
                heard, quiet = False, 0.0


async def polite(ws, clip: np.ndarray) -> None:
    voice = _Voice(ws)
    heard, quiet, talk, wait = False, 0.0, 0.0, 0.5
    async for frame, loud in _mic(ws):
        dt = len(frame) / d.IN_SR
        if voice.busy():
            talk = talk + dt if loud else max(0.0, talk - dt)
            if talk >= 0.3:
                await voice.stop()
                heard, quiet, talk, wait = True, 0.0, 0.0, 0.8
            continue
        if loud:
            heard, quiet = True, 0.0
        elif heard:
            quiet += dt
            if quiet >= wait - 1e-9:
                voice.say(clip)
                heard, quiet, talk, wait = False, 0.0, 0.0, 0.5


KINDS = {"silent": silent, "chatterbox": chatterbox, "clockwork": clockwork, "polite": polite}


async def serve(kind: str, clip: np.ndarray, host: str = "127.0.0.1", port: int = 0):
    """偽のシステムを起こす。返り値の sockets[0].getsockname()[1] がポート。"""

    async def handler(ws) -> None:
        try:
            await KINDS[kind](ws, clip)
        except websockets.ConnectionClosed:
            pass

    return await websockets.serve(handler, host, port, max_size=None)
