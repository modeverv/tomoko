"""MaAI 内部用の音声時間軸。24 kHz の位置を保ち、切り出すときに 16 kHz へ線形補間する。"""

from __future__ import annotations

from collections import deque
from math import ceil

import numpy as np

from engine.dto import IN_SR, OUT_SR


class _TomokoAudio:
    def __init__(self) -> None:
        # 24 kHz のサンプル位置。マイクの 1 サンプルはここでは正確に 1.5。
        self._segments: deque[tuple[float, float, np.ndarray]] = deque()
        self._end = 0.0

    def add(self, samples: np.ndarray, at_mic_s: float) -> None:
        if not len(samples):
            return
        start = max(round(at_mic_s * IN_SR) * OUT_SR / IN_SR, self._end)
        self._end = start + len(samples)
        self._segments.append((start, self._end, samples.copy()))

    def stop(self, at_mic_s: float) -> None:
        cut = round(at_mic_s * IN_SR) * OUT_SR / IN_SR
        kept: deque[tuple[float, float, np.ndarray]] = deque()
        for start, end, samples in self._segments:
            if start >= cut:
                break
            if end > cut:
                end, samples = cut, samples[:ceil(cut - start)].copy()
            kept.append((start, end, samples))
        self._segments = kept
        self._end = min(self._end, cut)

    def take(self, start_sample: int, frame_samples: int) -> np.ndarray:
        positions = np.arange(start_sample, start_sample + frame_samples) * (OUT_SR / IN_SR)
        end_position = (start_sample + frame_samples) * OUT_SR / IN_SR
        audio = np.zeros(frame_samples, dtype=np.float32)
        while self._segments and self._segments[0][1] <= positions[0]:
            self._segments.popleft()
        for index, (start, end, samples) in enumerate(self._segments):
            if start >= end_position:
                break
            mask = (positions >= start) & (positions < end)
            source_positions = np.arange(len(samples), dtype=np.float64)
            # 既知の隣接断片とは補間する。未着なら右端保持し、過去の入力は書き換えない。
            if index + 1 < len(self._segments):
                next_start, _, following = self._segments[index + 1]
                if end == next_start:
                    source_positions = np.r_[source_positions, next_start - start]
                    samples = np.r_[samples, following[0]]
            audio[mask] = np.interp(positions[mask] - start, source_positions, samples)
        while self._segments and self._segments[0][1] <= end_position:
            self._segments.popleft()
        return audio
