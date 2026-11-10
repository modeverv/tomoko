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
                 sustain_s: float | None = None) -> None:
        self._prob = prob
        self._threshold = threshold
        self._silence_n = round(silence_s * IN_SR / WINDOW)
        self._min_voiced_n = round(min_voiced_s * IN_SR / WINDOW)
        # 声がこの長さだけ続いたら "sustained" を 1 回出す。割り込みと、咳や相づちを分けるのに使う
        self._sustain_n = None if sustain_s is None else max(1, round(sustain_s * IN_SR / WINDOW))
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
            return []
        self._quiet += 1
        if self._quiet < self._silence_n:
            return []
        speech, voiced = self._speech, self._voiced
        self._speech = None
        end_s = t0 + WINDOW / IN_SR - self._quiet * WINDOW / IN_SR
        if voiced < self._min_voiced_n:
            return [VadEvent("drop", end_s)]
        audio = np.concatenate(speech[: len(speech) - self._quiet])
        return [VadEvent("end", end_s, Utterance(audio, self._start_s, end_s))]
