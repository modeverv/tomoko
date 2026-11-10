"""台本を声にする。macOS の say を外部プログラムとして呼ぶ。

say で作った音声は Apple の使用許諾により公開できない（_docs/licenses.md）。
生成物は work/ に置き、Git に入れない。
"""

import hashlib
import os
import subprocess
from pathlib import Path

import numpy as np

from bench import definitions as d
from bench.audio import read_wav, trim_silence, write_wav

VOICES = ("Kyoko", "Flo", "Eddy", "Sandy", "Reed")


def pick_voice(key: str) -> str:
    """シナリオの id から決まる話者。同じ id なら毎回同じ。"""
    return VOICES[int(hashlib.sha1(key.encode()).hexdigest(), 16) % len(VOICES)]


def synthesize(text: str, voice: str, cache: Path) -> np.ndarray:
    """16 kHz、前後の無音なし、最大振幅 0.5 の声を返す。同じ入力は cache から読む。"""
    name = hashlib.sha1(f"{voice}|{text}".encode()).hexdigest()[:20]
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
