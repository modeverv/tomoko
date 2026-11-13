"""MOCK: 初回音声の保留を、合成音声・偽ワーカー・入力時計で試すための部品。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import numpy as np
from test_engine_maai_session import FakeMaai

from engine import baseline
from engine.dto import IN_SR, OUT_SR, EngineConfig, MaaiFrame
from engine.sensors.vad import WINDOW, VadSensor
from scripts.diagnose_reply_race import _ClockLoop, _NumericTrace


def voice(windows: int, level: float = 0.5) -> np.ndarray:
    return np.full(WINDOW * windows, level, dtype=np.float32)


def quiet(windows: int) -> np.ndarray:
    return voice(windows, 0.0)


async def drain() -> None:
    for _ in range(16):
        await asyncio.sleep(0)


class HoldRig:
    def __init__(self, clock: _ClockLoop, *, streaming: bool = False,
                 phrases: int = 1, **config: object) -> None:
        self.clock, self.trace = clock, _NumericTrace()
        self.cfg = EngineConfig(**config)
        self.vad = VadSensor(lambda audio: float(np.max(np.abs(audio)) > 0.1),
                             self.cfg.vad_threshold, self.cfg.vad_silence_s,
                             self.cfg.vad_preroll_s, self.cfg.vad_min_voiced_s,
                             sustain_s=self.cfg.barge_in_voiced_s)
        self.tts_gate, self.later_gate, self.stream_tail_gate = (
            asyncio.Event(), asyncio.Event(), asyncio.Event())
        self.stream_closed = asyncio.Event()
        self.later_gate.set()
        self.stream_tail_gate.set()
        self.tts_calls = 0
        self.phrases = phrases
        self.heard: list[np.ndarray] = []
        self.prompts: list[list[dict]] = []
        self.sent: list[dict] = []
        self.messages: list[dict] = []
        self.maai = FakeMaai()
        self.session = baseline.BaselineSession(
            self.cfg, self.vad, self.trace, self.transcribe, self.reply,
            self.synthesize, self.send_audio, self.send_json,
            synthesize_stream=self.stream if streaming else None,
            backchannel_clip=np.full(7200, 0.1, dtype=np.float32))
        self.session.attach_maai(self.maai)

    async def transcribe(self, audio: np.ndarray) -> str:
        self.heard.append(audio.copy())
        return "合成入力"

    async def reply(self, messages: list[dict]) -> AsyncIterator[str]:
        self.prompts.append(messages)
        for _ in range(self.phrases):
            yield "合成返答。"

    async def synthesize(self, phrase: str) -> np.ndarray:
        self.tts_calls += 1
        await (self.tts_gate if self.tts_calls == 1 else self.later_gate).wait()
        return np.full(OUT_SR, 0.1, dtype=np.float32)

    async def stream(self, phrase: str) -> AsyncIterator[np.ndarray]:
        try:
            audio = await self.synthesize(phrase)
            yield audio[:4800]
            await self.stream_tail_gate.wait()
            yield audio[4800:]
        finally:
            await asyncio.sleep(0)  # MOCK: 非同期のストリーム後始末も完了を待つ。
            self.stream_closed.set()

    async def send_audio(self, generation: int, seq: int, audio: np.ndarray) -> None:
        self.sent.append({"generation": generation, "seq": seq, "samples": len(audio),
                          "at_s": self.clock.now_s, "speaking": self.vad.speaking})

    async def send_json(self, message: dict) -> None:
        self.messages.append(message)

    def events(self, name: str) -> list[dict]:
        return [event for event in self.trace.events if event["event"] == name]

    def feed_batch(self, audio: np.ndarray) -> None:
        self.clock.now_s += len(audio) / IN_SR
        self.session.on_mic(audio)

    async def feed(self, audio: np.ndarray) -> None:
        for offset in range(0, len(audio), WINDOW):
            self.feed_batch(audio[offset:offset + WINDOW])
            await drain()

    async def prepare_reply(self) -> None:
        await self.feed(voice(32, 0.3))
        await self.feed(quiet(12))
        assert self.tts_calls == 1

    async def ready_audio(self) -> None:
        self.tts_gate.set()
        await drain()

    def frame(self, **values: object) -> None:
        self.maai.frame = MaaiFrame(at_s=self.trace.audio_s, **values)
        self.session.on_maai(self.maai.frame)
