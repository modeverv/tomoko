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

MaAI をつなぐと（P3、attach_maai。どれも設定で 1 つずつ入れる）:
- backchannel: ユーザーが話している最中、相づちの予測（p_bc）が上がったあとの短い無音で「うん」と打つ。
  相づちは会話の記録に入れず、返事とは別の世代で送る。返事を作っている最中や、声を出している最中は打たない。
- maai_endpoint: VAD の言い終わりの判定を、ターンの予測（vap）で早めたり延ばしたりする。
- barge_guard: 声を出している最中のユーザーの声が相づちらしい（bc_det）なら、止めずに聞き流す。
  長く続いたら止める。
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

import numpy as np

from engine.dto import IN_SR, OUT_SR, EngineConfig, MaaiFrame, Utterance
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
                 synthesize_stream: Callable[[str], AsyncIterator[np.ndarray]] | None = None,
                 backchannel_clip: np.ndarray | None = None) -> None:
        self._cfg, self._vad, self._trace = cfg, vad, trace
        self._transcribe, self._reply, self._synthesize = transcribe, reply, synthesize
        self._send_audio, self._send_json = send_audio, send_json
        self._synthesize_stream = synthesize_stream
        self._queue: asyncio.Queue[Utterance] = asyncio.Queue()
        self._history: list[dict] = []
        self._generation = -1  # いまの返事の世代（止めるときに使う）
        self._generations = -1  # 送った声の世代の通し番号。返事と相づちで共有し、同じ番号を 2 度使わない
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
        # MaAI（attach_maai でつなぐ）
        self._maai = None
        self._bc_clip = backchannel_clip
        self._bc_armed_until = -1.0  # 相づちの機会の期限（マイクの時計）
        self._bc_armed_frame = -1.0  # 機会とみなした予測の時刻。同じ予測で期限を延ばさない
        self._bc_last = float("-inf")  # 前の相づちの時刻
        self._guard_hold = False  # 相づちとみなして、止めずにいる
        self._worker = asyncio.create_task(self._work())

    # ---- 受信ループから呼ぶ。ここでは待たない（v2 は、ここで LLM と TTS を待って詰まった） ----

    def attach_maai(self, maai) -> None:
        """MaAI のセンサーをつなぐ。マイクの音と、送った声を渡し、予測は on_maai で受け取る。"""
        self._maai = maai
        if self._cfg.maai_endpoint:
            self._vad.set_end_policy(self._decide_end, self._cfg.ep_min_silence_s,
                                     self._cfg.ep_max_silence_s)

    def on_mic(self, samples: np.ndarray) -> None:
        self._trace.audio_s += len(samples) / IN_SR
        if self._maai is not None:
            dropped = self._maai.feed_mic(samples)
            if dropped:
                self._trace.event("maai_dropped", frames=dropped)
        for ev in self._vad.feed(samples):
            if ev.kind == "end":
                self._trace.event("vad_end", speech_start_s=round(ev.utterance.start_s, 3),
                                  speech_end_s=round(ev.utterance.end_s, 3))
                self._on_utterance(ev.utterance)
                continue
            self._trace.event(f"vad_{ev.kind}", at_s=round(ev.at_s, 3))
            if ev.kind == "start":
                self._speech_started_while_playing, self._sustained = self._playing(), False
                self._guard_hold = False
            elif ev.kind == "sustained":
                self._sustained = True
                if self._cfg.barge_in:
                    if self._looks_like_backchannel(ev.at_s):
                        self._guard_hold = True
                        self._trace.event("barge_guard_hold", turn=self._turn)
                    else:
                        self._on_barge_in()
            elif ev.kind == "sustained_max" and self._guard_hold:
                # 相づちにしては長い。やはり割り込みとして止める
                self._guard_hold = False
                self._trace.event("barge_guard_release", turn=self._turn)
                self._on_barge_in()
        if self._maai is not None:
            self._maybe_backchannel(self._maai.latest())

    def on_maai(self, frame: MaaiFrame) -> None:
        """MaAI の予測が 1 つ届いた（イベントループの上で呼ばれる）。"""
        self._trace.event("maai", **_frame_fields(frame))
        self._maybe_backchannel(frame)

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
        if self._maai is not None:
            await asyncio.to_thread(self._maai.close)
        self._trace.close()

    # ---- MaAI ----

    def _fresh_frame(self, now: float) -> MaaiFrame | None:
        frame = None if self._maai is None else self._maai.latest()
        if frame is None or now - frame.at_s > self._cfg.maai_stale_s:
            return None
        return frame

    def _decide_end(self, silence_s: float) -> bool | None:
        """VAD から、無音の途中で聞かれる。True は言い終わり、False は待つ、None は既定に従う。"""
        frame = self._fresh_frame(self._vad.now_s)
        values = None if frame is None else (
            frame.p_now if self._cfg.ep_signal == "p_now" else frame.p_future)
        if values is None:
            return None
        tomoko = values[1]
        if tomoko >= self._cfg.ep_shift_p:
            return True
        if tomoko <= self._cfg.ep_hold_p:
            return False
        return None

    def _looks_like_backchannel(self, now: float) -> bool:
        """声を出している最中のユーザーの声が、相づちらしいか（bc_det）。"""
        if not (self._cfg.barge_guard and self._speech_started_while_playing and self._playing()):
            return False
        frame = self._fresh_frame(now)
        return (frame is not None and frame.p_bc_det_user is not None
                and frame.p_bc_det_user >= self._cfg.guard_bc_det)

    def _maybe_backchannel(self, frame: MaaiFrame | None) -> None:
        cfg = self._cfg
        if not cfg.backchannel or self._bc_clip is None or frame is None or frame.p_bc is None:
            return
        now = self._vad.now_s
        if frame.p_bc >= cfg.bc_threshold and frame.at_s > self._bc_armed_frame:
            # p_bc は、相づちを打つべき時刻より先に上がる。少しの間、ユーザーの短い無音を待つ
            self._bc_armed_until, self._bc_armed_frame = frame.at_s + cfg.bc_arm_s, frame.at_s
        vad = self._vad
        if (now > self._bc_armed_until or not vad.speaking
                or vad.quiet_s + 1e-9 < cfg.bc_quiet_s
                or now - vad.start_s < cfg.bc_min_turn_s
                or now - self._bc_last < cfg.bc_cooldown_s
                or self._playing()
                or (self._answering is not None and not self._answering.done())):
            return
        if frame.p_now is not None and frame.p_now[1] >= cfg.bc_max_shift_p:
            return  # 言い終わりらしい。相づちではなく答える場面
        self._bc_last, self._bc_armed_until = now, -1.0
        generation = self._new_generation()
        self._trace.event("backchannel", generation=generation, text=cfg.bc_text,
                          p_bc=round(frame.p_bc, 3), quiet_s=round(vad.quiet_s, 3))
        if self._maai is not None:
            self._maai.add_tomoko_audio(self._bc_clip, self._trace.audio_s)
        asyncio.get_running_loop().create_task(self._send_clip(generation, self._bc_clip))

    async def _send_clip(self, generation: int, clip: np.ndarray) -> None:
        piece = round(self._cfg.send_chunk_s * OUT_SR)
        for seq, i in enumerate(range(0, len(clip), piece)):
            await self._send_audio(generation, seq, clip[i : i + piece])

    def _new_generation(self) -> int:
        self._generations += 1
        return self._generations

    # ---- 割り込み ----

    def _playing(self) -> bool:
        return asyncio.get_running_loop().time() < self._play_end

    def _on_utterance(self, utterance: Utterance) -> None:
        if self._cfg.barge_in and self._speech_started_while_playing and not self._sustained:
            # 声が出ている最中の短い声。相づちとして聞き流す
            self._trace.event("ignored_short_voice", speech_end_s=round(utterance.end_s, 3))
            return
        if self._guard_hold:
            # 相づちらしいので止めずにいた声が、止める長さに届く前に終わった。答えない
            self._guard_hold = False
            self._trace.event("ignored_backchannel", speech_end_s=round(utterance.end_s, 3))
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
            if self._maai is not None:
                self._maai.stop_tomoko(self._trace.audio_s)
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

        # 世代は、この返事のあいだ変えない（相づちが途中で新しい世代を使っても混ざらない）
        generation = self._generation = self._new_generation()
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
                    if self._maai is not None:
                        self._maai.add_tomoko_audio(audio, self._trace.audio_s)
                    for i in range(0, len(audio), piece):
                        await self._send_audio(generation, seq, audio[i : i + piece])
                        seq += 1
                    length += len(audio)
                tr.event("tts_done", turn=turn, phrase=n, audio_len_s=round(length / OUT_SR, 3))
                tr.event("sent", turn=turn, phrase=n, generation=generation)
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


def _frame_fields(frame: MaaiFrame) -> dict:
    """記録用。2 要素の値は (ユーザー, Tomoko)。"""
    out: dict = {"at_s": round(frame.at_s, 3)}
    for name in ("p_now", "p_future"):
        values = getattr(frame, name)
        if values is not None:
            out[name] = [round(v, 3) for v in values]
    for name in ("p_bc", "p_bc_det_user", "vad_user", "vad_tomoko"):
        value = getattr(frame, name)
        if value is not None:
            out[name] = round(value, 3)
    return out


def _join(first: Utterance, second: Utterance) -> Utterance:
    """2 つの発話を、間の無音ごとつないで 1 つにする。"""
    gap = np.zeros(max(0, round((second.start_s - first.end_s) * IN_SR)), dtype=np.float32)
    gap = gap[: IN_SR]  # 長い間は 1 秒に詰める
    return Utterance(np.concatenate([first.audio, gap, second.audio]), first.start_s, second.end_s)
