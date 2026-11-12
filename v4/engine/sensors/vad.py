"""VAD。話しているか、無音がどれだけ続いたかを見て、発話を切り出す。

v2 の経験: Silero は声の頭を拾うのが遅く、発話の最初が欠ける。手前の音を残しておく。
窓は 512 サンプル（32 ms）なので、無音の閾値は 32 ms 単位に丸まる。
"""

from collections import deque
from collections.abc import Callable

import numpy as np

from engine.dto import IN_SR, Utterance, VadEvent

WINDOW = 512


def silero_prob() -> Callable[[np.ndarray], float]:
    """接続ごとに 1 つ作る。Silero は内部に状態を持つ。"""
    import torch
    from silero_vad import load_silero_vad

    model = load_silero_vad()

    def prob(window: np.ndarray) -> float:
        return float(model(torch.from_numpy(window), IN_SR).item())

    return prob


class VadSensor:
    def __init__(self, prob: Callable[[np.ndarray], float], threshold: float = 0.5,
                 silence_s: float = 0.4, preroll_s: float = 0.5, min_voiced_s: float = 0.2,
                 sustain_s: float | None = None, sustain_max_s: float | None = None) -> None:
        self._prob = prob
        self._threshold = threshold
        self._silence_n = round(silence_s * IN_SR / WINDOW)
        self._min_voiced_n = round(min_voiced_s * IN_SR / WINDOW)
        # 声がこの長さだけ続いたら "sustained" を 1 回出す。割り込みと、咳や相づちを分けるのに使う
        self._sustain_n = None if sustain_s is None else max(1, round(sustain_s * IN_SR / WINDOW))
        # 声がさらに長く続いたら "sustained_max" を 1 回出す。相づちとみなして止めずにいた声を、やはり止めるのに使う
        self._sustain_max_n = (None if sustain_max_s is None
                               else max(1, round(sustain_max_s * IN_SR / WINDOW)))
        if (self._sustain_n is not None and self._sustain_max_n is not None
                and self._sustain_max_n <= self._sustain_n):
            raise ValueError("sustain_max_s は sustain_s より、窓 1 つ以上長くする")
        # 言い終わりの判定を外から変える口（P3、MaAI）。None なら無音 silence_s で言い終わり
        self._decide_end: Callable[[float], bool | None] | None = None
        self._min_quiet_n = self._max_quiet_n = self._silence_n
        self._preroll: deque[np.ndarray] = deque(maxlen=max(1, round(preroll_s * IN_SR / WINDOW)))
        self._rest = np.zeros(0, dtype=np.float32)
        self._windows = 0  # これまでに見た窓の数
        self._speech: list[np.ndarray] | None = None
        self._start_s = 0.0
        self._quiet = 0
        self._voiced = 0

    @property
    def speaking(self) -> bool:
        return self._speech is not None

    @property
    def now_s(self) -> float:
        """処理し終えた窓の終わりの時刻（入力の先頭からの秒）。"""
        return self._windows * WINDOW / IN_SR

    @property
    def start_s(self) -> float:
        """いまの発話の始まり。話していないときは、最後の発話の始まり。"""
        return self._start_s

    @property
    def quiet_s(self) -> float:
        """話している途中の、いまの無音の長さ。話していないときは 0。"""
        return self._quiet * WINDOW / IN_SR if self._speech is not None else 0.0

    def set_end_policy(self, decide: Callable[[float], bool | None] | None,
                       min_silence_s: float, max_silence_s: float) -> None:
        """言い終わりの判定を外から変える。

        無音が min_silence_s に達してからは、窓ごとに decide(無音の秒) を聞く。True なら言い終わり、
        False なら待つ、None なら既定（silence_s）に従う。max_silence_s の無音で、必ず言い終わりとする。
        言い終わりは 1 発話に 1 回だけ出す（途中で切り出して、あとで取り消すことはしない）。
        """
        self._decide_end = decide
        if decide is None:  # 外す（MaAI が壊れたときなど）。既定の silence_s に戻る
            return
        self._min_quiet_n = max(1, round(min_silence_s * IN_SR / WINDOW))
        self._max_quiet_n = max(self._min_quiet_n, round(max_silence_s * IN_SR / WINDOW))

    def feed(self, samples: np.ndarray) -> list[VadEvent]:
        """マイクの音を足す。届く長さは何でもよい（ブラウザは 128、ベンチは 320 サンプル）。"""
        self._rest = np.concatenate([self._rest, samples.astype(np.float32)])
        events: list[VadEvent] = []
        while len(self._rest) >= WINDOW:
            window, self._rest = self._rest[:WINDOW], self._rest[WINDOW:]
            events += self._step(window)
        return events

    def _step(self, window: np.ndarray) -> list[VadEvent]:
        t0 = self._windows * WINDOW / IN_SR
        self._windows += 1
        loud = self._prob(window) >= self._threshold
        if self._speech is None:
            if not loud:
                self._preroll.append(window)
                return []
            self._speech = [*self._preroll, window]
            self._preroll.clear()
            self._start_s, self._quiet, self._voiced = t0, 0, 1
            return [VadEvent("start", t0)]
        self._speech.append(window)
        if loud:
            self._quiet, self._voiced = 0, self._voiced + 1
            if self._voiced == self._sustain_n:
                return [VadEvent("sustained", t0 + WINDOW / IN_SR)]
            if self._voiced == self._sustain_max_n:
                return [VadEvent("sustained_max", t0 + WINDOW / IN_SR)]
            return []
        self._quiet += 1
        if not self._ends_now():
            return []
        speech, voiced = self._speech, self._voiced
        self._speech = None
        end_s = t0 + WINDOW / IN_SR - self._quiet * WINDOW / IN_SR
        if voiced < self._min_voiced_n:
            return [VadEvent("drop", end_s)]
        audio = np.concatenate(speech[: len(speech) - self._quiet])
        return [VadEvent("end", end_s, Utterance(audio, self._start_s, end_s))]

    def _ends_now(self) -> bool:
        if self._decide_end is None:
            return self._quiet >= self._silence_n
        if self._quiet < self._min_quiet_n:
            return False
        if self._quiet >= self._max_quiet_n:
            return True
        verdict = self._decide_end(self._quiet * WINDOW / IN_SR)
        return self._quiet >= self._silence_n if verdict is None else verdict
