"""台本を声にする。macOS の say を外部プログラムとして呼ぶ。

say で作った音声は Apple の使用許諾により公開できない（_docs/licenses.md）。
生成物は work/ に置き、Git に入れない。
"""

from __future__ import annotations

import hashlib
import math
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import numpy as np

from bench import definitions as d
from bench.audio import read_wav, trim_silence, write_wav

VOICES = ("Kyoko", "Flo", "Eddy", "Sandy", "Reed")
Synthesizer = Callable[[str, str, Path], np.ndarray]
VOICE_THRESHOLD = 0.005  # trim_silence と同じ音量で注釈の端をそろえる
VOICE_MERGE_GAP_S = 0.15


def pick_voice(key: str) -> str:
    """シナリオの id から決まる話者。同じ id なら毎回同じ。"""
    return VOICES[int(hashlib.sha1(key.encode()).hexdigest(), 16) % len(VOICES)]


def synthesize(text: str, voice: str, cache: Path) -> np.ndarray:
    """16 kHz、前後の無音なし、最大振幅 0.5 の声を返す。同じ入力は cache から読む。"""
    return _synthesize(text, voice, cache)


def synthesize_one_pass(
    parts: list[str | float], voice: str, cache: Path, *, synth: Synthesizer | None = None,
) -> np.ndarray:
    """区切りを say の無音コマンドにして、1 発話を 1 回で合成する。

    synth はテスト用の差し替え。synthesize と同じ形式の音を返す関数を渡す。
    句点などを取り除かないので、声が持つ自然な間も出力に残る。
    """
    text = "".join(
        f"[[slnc {round(part * 1000)}]]" if isinstance(part, float) else part
        for part in parts
    )
    if synth is not None:
        return synth(text, voice, cache)
    return _synthesize(text, voice, cache, one_pass=True)


def _synthesize(text: str, voice: str, cache: Path, *, one_pass: bool = False) -> np.ndarray:
    key = f"{voice}|{text}"
    if one_pass:
        key = f"one-pass|{key}"
    name = hashlib.sha1(key.encode()).hexdigest()[:20]
    path = cache / f"{name}.wav"
    if not path.exists():
        # 複数のプロセスが同時に同じ声を作っても壊れないように、作業ファイルの名前を分ける
        raw = cache / f"{name}.{os.getpid()}.raw.wav"
        cache.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["say", "-v", voice, "-o", str(raw), "--file-format=WAVE",
             f"--data-format=LEI16@{d.IN_SR}", text],
            check=True,
        )
        samples, sr = read_wav(raw)
        raw.unlink()
        assert sr == d.IN_SR
        samples = trim_silence(samples)
        if len(samples) == 0:
            raise RuntimeError(f"say が無音を返した: voice={voice} text={text!r}")
        samples = samples / np.max(np.abs(samples)) * 0.5
        tmp = cache / f"{name}.{os.getpid()}.wav"
        write_wav(tmp, samples, sr)
        tmp.replace(path)
    return read_wav(path)[0]


def annotate_one_pass(
    samples: np.ndarray, requested_pauses: list[float], *, label: str,
) -> tuple[list[list[float]], list[list[float]]]:
    """音量から voiced と台本の pauses を取る。label は台本と発話の識別用。"""
    loud = (np.abs(samples) > VOICE_THRESHOLD).astype(np.int8)
    edges = np.diff(loud, prepend=0, append=0)
    starts = np.flatnonzero(edges == 1).tolist()
    ends = np.flatnonzero(edges == -1).tolist()
    gaps = list(zip(ends[:-1], starts[1:]))  # 先頭・末尾の無音は候補にしない
    pauses = sorted(sorted(gaps, key=lambda x: x[1] - x[0], reverse=True)[:len(requested_pauses)])
    for i, requested in enumerate(requested_pauses):
        context = f"{label} pause {i + 1} (要求 {requested}s)"
        if i >= len(pauses):
            raise ValueError(f"{context}: 区切りの無音が見つからない")
        start, end = pauses[i]
        # 注釈の小数 4 桁への丸めで、1 サンプル短い区切りを合格させない。
        minimum_samples = math.ceil(0.8 * requested * d.IN_SR - 1e-9)
        if end - start < minimum_samples:
            raise ValueError(f"{context}: 無音 {(end - start) / d.IN_SR:.6f}s が要求の 0.8 倍未満")
    if not starts:
        raise ValueError(f"{label}: 声のある区間が見つからない")
    voiced: list[list[int]] = []
    for start, end in zip(starts, ends):
        if voiced and start - voiced[-1][1] < VOICE_MERGE_GAP_S * d.IN_SR:
            voiced[-1][1] = end
        else:
            voiced.append([start, end])
    return (
        [[round(a / d.IN_SR, 4), round(b / d.IN_SR, 4)] for a, b in voiced],
        [[round(a / d.IN_SR, 4), round(b / d.IN_SR, 4)] for a, b in pauses],
    )
