"""P2 の基準線。いちばん単純な通しの経路。賢い仕組みは入れない。

  VAD（無音 400 ms で言い終わり）→ STT → 手前の LLM → TTS → 送信

- 話している最中にユーザーが話しても、止まらない。その発話は順番待ちに入り、あとで答える。
- 相づちは打たない。短い声にも 1 発話として答える。
P3 で、この経路を指揮者が働き手を起こす形に組み直す。
"""

import asyncio
from collections.abc import Awaitable, Callable

import numpy as np

from engine.dto import IN_SR, OUT_SR, EngineConfig, Utterance
from engine.front_llm import PhraseSplitter
from engine.sensors.vad import VadSensor
from engine.trace import Trace

SendAudio = Callable[[int, int, np.ndarray], Awaitable[None]]
SendJson = Callable[[dict], Awaitable[None]]


class BaselineSession:
    def __init__(self, cfg: EngineConfig, vad: VadSensor, trace: Trace,
                 transcribe: Callable[[np.ndarray], Awaitable[str]],
                 reply: Callable[[list[dict]], "asyncio.AsyncIterator[str]"],
                 synthesize: Callable[[str], Awaitable[np.ndarray]],
                 send_audio: SendAudio, send_json: SendJson) -> None:
        self._cfg, self._vad, self._trace = cfg, vad, trace
        self._transcribe, self._reply, self._synthesize = transcribe, reply, synthesize
        self._send_audio, self._send_json = send_audio, send_json
        self._queue: asyncio.Queue[Utterance] = asyncio.Queue()
        self._history: list[dict] = []
        self._generation = -1
        self._turn = 0
        self._worker = asyncio.create_task(self._work())

    def on_mic(self, samples: np.ndarray) -> None:
        """受信ループから呼ぶ。ここでは待たない（v2 は、ここで LLM と TTS を待って詰まった）。"""
        self._trace.audio_s += len(samples) / IN_SR
        for ev in self._vad.feed(samples):
            if ev.kind == "end":
                self._trace.event("vad_end", speech_start_s=round(ev.utterance.start_s, 3),
                                  speech_end_s=round(ev.utterance.end_s, 3))
                self._queue.put_nowait(ev.utterance)
            else:
                self._trace.event(f"vad_{ev.kind}", at_s=round(ev.at_s, 3))

    def on_playback(self, report: dict) -> None:
        self._trace.event("playback", **{k: report.get(k) for k in ("generation", "played_s")})

    async def close(self) -> None:
        self._worker.cancel()
        try:
            await self._worker
        except asyncio.CancelledError:
            pass
        self._trace.close()

    async def _work(self) -> None:
        while True:
            utterance = await self._queue.get()
            self._turn += 1
            try:
                await self._answer(self._turn, utterance)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001  1 発話の失敗で会話を止めない。黙って消さず、記録する
                self._trace.event("turn_failed", turn=self._turn, error=f"{type(e).__name__}: {e}")

    async def _answer(self, turn: int, utterance: Utterance) -> None:
        tr = self._trace
        tr.event("stt_start", turn=turn, audio_len_s=round(len(utterance.audio) / IN_SR, 3))
        text = await self._transcribe(utterance.audio)
        tr.event("stt_final", turn=turn, text=text)
        if not text:
            return
        await self._send_json({"type": "text", "role": "user", "text": text})
        self._history.append({"role": "user", "content": text})

        self._generation += 1
        phrases: asyncio.Queue[str | None] = asyncio.Queue()

        async def think() -> str:
            splitter, full, first = PhraseSplitter(self._cfg.first_cut_min_chars), "", True
            tr.event("llm_start", turn=turn)
            try:
                async for delta in self._reply(list(self._history)):
                    if first:
                        tr.event("llm_first_token", turn=turn)
                        first = False
                    full += delta
                    for p in splitter.feed(delta):
                        phrases.put_nowait(p)
                for p in splitter.flush():
                    phrases.put_nowait(p)
            finally:
                phrases.put_nowait(None)
            tr.event("llm_done", turn=turn, text=full)
            return full

        thinking = asyncio.create_task(think())
        seq, piece, n = 0, round(self._cfg.send_chunk_s * OUT_SR), 0
        try:
            while (phrase := await phrases.get()) is not None:
                tr.event("tts_start", turn=turn, phrase=n, text=phrase)
                audio = await self._synthesize(phrase)
                tr.event("tts_done", turn=turn, phrase=n, audio_len_s=round(len(audio) / OUT_SR, 3))
                for i in range(0, len(audio), piece):
                    await self._send_audio(self._generation, seq, audio[i : i + piece])
                    seq += 1
                tr.event("sent", turn=turn, phrase=n, generation=self._generation)
                n += 1
            full = await thinking
        finally:
            thinking.cancel()
        # 基準線では、作った文の全体を話した記録とする。実際に聞こえた範囲の記録は P3 で入れる
        self._history.append({"role": "assistant", "content": full})
        await self._send_json({"type": "text", "role": "tomoko", "text": full})
