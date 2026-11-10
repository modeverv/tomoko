"""通しの経路。P2 の基準線に、P3 の変更を設定で 1 つずつ足していく。

  VAD → STT → 手前の LLM → TTS → 送信

設定をすべて既定のままにすると、P2 の基準線と同じ動きになる:
- 話している最中にユーザーが話しても、止まらない。その発話は順番待ちに入り、あとで答える。
- 相づちは打たない。短い声にも 1 発話として答える。

barge_in を入れると（P3）:
- 返事の声がまだ出ていないうちにユーザーが話を続けたら、作りかけの返事を捨てる。
  前の発話と続きの発話をつないで、1 つの発話として答える（言いよどみ、文と文の間の無音）。
- 返事の声が出ている最中にユーザーが話し始めたら、声を止める。話した分だけを会話の記録に残す。
- 声が出ている最中の短い声（相づち）には、止まらず、答えもしない。
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

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
                 reply: Callable[[list[dict]], AsyncIterator[str]],
                 synthesize: Callable[[str], Awaitable[np.ndarray]],
                 send_audio: SendAudio, send_json: SendJson,
                 synthesize_stream: Callable[[str], AsyncIterator[np.ndarray]] | None = None) -> None:
        self._cfg, self._vad, self._trace = cfg, vad, trace
        self._transcribe, self._reply, self._synthesize = transcribe, reply, synthesize
        self._send_audio, self._send_json = send_audio, send_json
        self._synthesize_stream = synthesize_stream
        self._queue: asyncio.Queue[Utterance] = asyncio.Queue()
        self._history: list[dict] = []
        self._generation = -1
        self._turn = 0
        # いまの返事の状態。割り込みの判断に使う
        self._answering: asyncio.Task | None = None
        self._utterance: Utterance | None = None  # いま答えている発話
        self._audible = False  # いまの返事の声を、1 断片でも送ったか
        self._spoken: list[str] = []  # いまの返事で、声にして送った句
        self._play_end = 0.0  # 送った声が鳴り終わる見込みの時刻（このプロセスの時計）
        self._carry: Utterance | None = None  # 捨てた返事の元の発話。次の発話の頭につなぐ
        self._speech_started_while_playing = False
        self._sustained = False  # いまの声が、割り込みとみなす長さまで続いたか
        self._user_text = ""
        self._closing = False
        self._worker = asyncio.create_task(self._work())

    # ---- 受信ループから呼ぶ。ここでは待たない（v2 は、ここで LLM と TTS を待って詰まった） ----

    def on_mic(self, samples: np.ndarray) -> None:
        self._trace.audio_s += len(samples) / IN_SR
        for ev in self._vad.feed(samples):
            if ev.kind == "end":
                self._trace.event("vad_end", speech_start_s=round(ev.utterance.start_s, 3),
                                  speech_end_s=round(ev.utterance.end_s, 3))
                self._on_utterance(ev.utterance)
                continue
            self._trace.event(f"vad_{ev.kind}", at_s=round(ev.at_s, 3))
            if ev.kind == "start":
                self._speech_started_while_playing, self._sustained = self._playing(), False
            elif ev.kind == "sustained":
                self._sustained = True
                if self._cfg.barge_in:
                    self._on_barge_in()

    def on_playback(self, report: dict) -> None:
        self._trace.event("playback", **{k: report.get(k) for k in ("generation", "played_s")})

    async def close(self) -> None:
        self._closing = True
        self._worker.cancel()
        try:
            await self._worker
        except asyncio.CancelledError:
            pass
        if self._answering is not None:
            self._answering.cancel()
        self._trace.close()

    # ---- 割り込み ----

    def _playing(self) -> bool:
        return asyncio.get_running_loop().time() < self._play_end

    def _on_utterance(self, utterance: Utterance) -> None:
        if self._cfg.barge_in and self._speech_started_while_playing and not self._sustained:
            # 声が出ている最中の短い声。相づちとして聞き流す
            self._trace.event("ignored_short_voice", speech_end_s=round(utterance.end_s, 3))
            return
        if self._carry is not None:
            utterance = _join(self._carry, utterance)
            self._carry = None
        self._queue.put_nowait(utterance)

    def _on_barge_in(self) -> None:
        answering = self._answering is not None and not self._answering.done()
        if answering and not self._audible:
            # まだ声を出していない。作りかけを捨て、続きの発話とつないで答え直す
            self._trace.event("discard_reply", turn=self._turn)
            self._carry = self._utterance
            self._answering.cancel()
        elif answering or self._playing():
            self._trace.event("stop", turn=self._turn, generation=self._generation)
            self._play_end = 0.0
            if answering:
                self._answering.cancel()
            asyncio.get_running_loop().create_task(
                self._send_json({"type": "stop", "generation": self._generation}))

    # ---- 働き手 ----

    async def _work(self) -> None:
        while True:
            utterance = await self._queue.get()
            if self._carry is not None:  # 捨てた返事の発話が、順番待ちの前に残っていた
                utterance, self._carry = _join(self._carry, utterance), None
            self._turn += 1
            self._utterance, self._audible, self._spoken = utterance, False, []
            before = len(self._history)
            self._answering = asyncio.create_task(self._answer(self._turn, utterance))
            try:
                await asyncio.shield(self._answering)
            except asyncio.CancelledError:
                if self._closing or not self._answering.cancelled():
                    raise
                # 割り込みで捨てた、または止めた
                del self._history[before:]
                if self._audible:
                    heard = "".join(self._spoken)
                    self._history.append({"role": "user", "content": self._user_text})
                    self._history.append({"role": "assistant", "content": heard})
                    await self._send_json({"type": "text", "role": "tomoko", "text": heard})
            except Exception as e:  # noqa: BLE001  1 発話の失敗で会話を止めない。黙って消さず、記録する
                self._trace.event("turn_failed", turn=self._turn, error=f"{type(e).__name__}: {e}")

    async def _answer(self, turn: int, utterance: Utterance) -> None:
        tr = self._trace
        tr.event("stt_start", turn=turn, audio_len_s=round(len(utterance.audio) / IN_SR, 3))
        text = await self._transcribe(utterance.audio)
        tr.event("stt_final", turn=turn, text=text)
        if not text:
            return
        self._user_text = text
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
        loop = asyncio.get_running_loop()
        try:
            while (phrase := await phrases.get()) is not None:
                tr.event("tts_start", turn=turn, phrase=n, text=phrase)
                length, first = 0, True
                async for audio in self._pieces(phrase):
                    if first:  # 最初の断片が届いた。丸ごと合成する TTS では、句の全体
                        tr.event("tts_first_audio", turn=turn, phrase=n)
                        self._audible, first = True, False
                        self._spoken.append(phrase)
                    self._play_end = max(self._play_end, loop.time()) + len(audio) / OUT_SR
                    for i in range(0, len(audio), piece):
                        await self._send_audio(self._generation, seq, audio[i : i + piece])
                        seq += 1
                    length += len(audio)
                tr.event("tts_done", turn=turn, phrase=n, audio_len_s=round(length / OUT_SR, 3))
                tr.event("sent", turn=turn, phrase=n, generation=self._generation)
                n += 1
            full = await thinking
        finally:
            thinking.cancel()
        # 止められなかった返事は、作った文の全体を話した記録とする
        self._history.append({"role": "assistant", "content": full})
        await self._send_json({"type": "text", "role": "tomoko", "text": full})

    async def _pieces(self, phrase: str) -> AsyncIterator[np.ndarray]:
        if self._synthesize_stream is not None:
            async for audio in self._synthesize_stream(phrase):
                yield audio
        else:
            yield await self._synthesize(phrase)


def _join(first: Utterance, second: Utterance) -> Utterance:
    """2 つの発話を、間の無音ごとつないで 1 つにする。"""
    gap = np.zeros(max(0, round((second.start_s - first.end_s) * IN_SR)), dtype=np.float32)
    gap = gap[: IN_SR]  # 長い間は 1 秒に詰める
    return Utterance(np.concatenate([first.audio, gap, second.audio]), first.start_s, second.end_s)
