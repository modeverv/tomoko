"""音声ファイルの読み書きと、小さな道具。"""

from pathlib import Path

import numpy as np
import soundfile as sf


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return data[:, 0].copy(), sr


def write_wav(path: Path, samples: np.ndarray, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), samples.astype(np.float32), sr, subtype="PCM_16")


def resample(samples: np.ndarray, src: int, dst: int) -> np.ndarray:
    """線形補間。VAD と書き起こしに渡すだけなので、これで足りる。"""
    if src == dst or len(samples) == 0:
        return samples.astype(np.float32)
    n = round(len(samples) * dst / src)
    x = np.linspace(0.0, len(samples) - 1, n)
    return np.interp(x, np.arange(len(samples)), samples).astype(np.float32)


def trim_silence(samples: np.ndarray, threshold: float = 0.005) -> np.ndarray:
    """前後の無音を落とす。注釈の時刻を、声の端にそろえるため。"""
    loud = np.flatnonzero(np.abs(samples) > threshold)
    if len(loud) == 0:
        return samples[:0]
    return samples[loud[0] : loud[-1] + 1]


def rms(samples: np.ndarray) -> float:
    if len(samples) == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
