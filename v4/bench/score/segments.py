"""録った出力から、Tomoko の発話区間を取る。時刻の採点の入口。"""

from functools import cache
from pathlib import Path

from bench import definitions as d
from bench.audio import read_wav, resample

Seg = tuple[float, float]


def merge(segs: list[Seg], gap: float = d.MERGE_GAP_S) -> list[Seg]:
    """短い切れ目をつないで、1 つの発話区間にする。"""
    out: list[Seg] = []
    for a, b in sorted(segs):
        if out and a - out[-1][1] < gap:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


@cache
def _model():
    from silero_vad import load_silero_vad

    return load_silero_vad()


def speech_segments(wav: Path) -> list[Seg]:
    import torch
    from silero_vad import get_speech_timestamps

    samples, sr = read_wav(wav)
    if len(samples) == 0:
        return []
    audio = torch.from_numpy(resample(samples, sr, 16_000))
    stamps = get_speech_timestamps(
        audio, _model(), sampling_rate=16_000, threshold=d.VAD_THRESHOLD,
        min_silence_duration_ms=d.VAD_MIN_SILENCE_MS, min_speech_duration_ms=d.VAD_MIN_SPEECH_MS,
        speech_pad_ms=d.VAD_PAD_MS, return_seconds=False,
    )
    return merge([(s["start"] / 16_000, s["end"] / 16_000) for s in stamps])
