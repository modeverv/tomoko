"""MaAI オフライン評価の入力。生成済み注釈と旧 run を同じ形にする。"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from bench.audio import read_wav
from bench.scenario import Manifest, Step

SAMPLE_RATE = 16000


@dataclass
class OfflineClip:
    id: str
    axis: str
    step: int | None
    audio: np.ndarray = field(repr=False)
    voiced: list[tuple[float, float]]
    events: list[dict]
    detection_voiced: list[tuple[float, float]] = field(default_factory=list)


def source_kind(path: Path) -> str:
    inputs = any(path.glob("*/scenario.json"))
    runs = any(path.glob("*/run.json"))
    if inputs == runs:
        raise ValueError("scenario.json または run.json を持つ子ディレクトリの組を指定する")
    return "inputs" if inputs else "runs"


def _read(path: Path) -> np.ndarray:
    audio, rate = read_wav(path)
    if rate != SAMPLE_RATE:
        raise ValueError(f"{path}: 16000 Hz が必要（{rate} Hz）")
    return audio.astype(np.float32)


def _pause_types(step: Step) -> list[str]:
    types = []
    text = ""
    for part in step.parts():
        if isinstance(part, str):
            text = part.rstrip().rstrip("」』）)\"' ")
        else:
            types.append(
                "comma"
                if text.endswith(("、", ","))
                else "period"
                if text.endswith(("。", ".", "！", "？", "!", "?"))
                else "other"
            )
            text = ""
    return types


def _generated(directory: Path, axes: set[str]) -> list[OfflineClip]:
    manifest = Manifest.load(directory)
    scenario = manifest.scenario
    if scenario.axis not in axes:
        return []
    indices = [0, len(manifest.audio) - 1] if scenario.axis in {"T3", "T4"} else [0]
    clips = []
    for index in indices:
        annotation = manifest.audio[index]
        audio = _read(directory / annotation.wav)
        duration = len(audio) / SAMPLE_RATE
        spans = annotation.voiced + annotation.pauses
        if any(not (0 <= a < b <= duration + 0.0001) for a, b in spans):
            raise ValueError(f"{directory}: 注釈の区間が音声の範囲外")
        types = _pause_types(scenario.steps[index])
        if len(types) != len(annotation.pauses):
            raise ValueError(f"{directory}: 台本と pauses の個数が違う")
        voiced = [(a + 1.0, b + 1.0) for a, b in annotation.voiced]
        events = [
            {
                "kind": "hold",
                "t": a + 1.0,
                "gap": round(b - a, 6),
                "pause_type": kind,
                "step": index,
            }
            for (a, b), kind in zip(annotation.pauses, types, strict=True)
        ]
        if voiced:
            events.append({"kind": "shift", "t": voiced[-1][1], "gap": None, "step": index})
        clips.append(
            OfflineClip(
                scenario.id,
                scenario.axis,
                index,
                np.pad(audio, (SAMPLE_RATE, 2 * SAMPLE_RATE)),
                voiced,
                events,
                voiced if scenario.axis in {"T3", "T4"} and index == indices[-1] else [],
            )
        )
    return clips


def _recorded(directory: Path, axes: set[str]) -> list[OfflineClip]:
    run = json.loads((directory / "run.json").read_text())
    if run["axis"] not in axes:
        return []
    # 旧入力だけは、従来どおり Silero で実音声の無音を検出する。
    from bench.score.segments import speech_segments

    audio = _read(directory / "input.wav")
    voiced = speech_segments(directory / "input.wav")
    events, detection = [], []
    for index, step in enumerate(run["steps"]):
        inside = [s for s in voiced if s[1] > step["start"] - 0.1 and s[0] < step["end"] + 0.1]
        for a, b in itertools.pairwise(inside):
            if b[0] - a[1] >= 0.2:
                events.append(
                    {
                        "kind": "hold",
                        "t": a[1],
                        "gap": b[0] - a[1],
                        "pause_type": "other",
                        "step": index,
                    }
                )
        if inside:
            events.append({"kind": "shift", "t": inside[-1][1], "gap": None, "step": index})
        if index == len(run["steps"]) - 1 and run["axis"] in {"T3", "T4"}:
            detection = inside
    return [OfflineClip(run["id"], run["axis"], None, audio, voiced, events, detection)]


def load_clips(path: Path, axes: set[str]) -> list[OfflineClip]:
    kind = source_kind(path)
    name = "scenario.json" if kind == "inputs" else "run.json"
    load = _generated if kind == "inputs" else _recorded
    return [clip for file in sorted(path.glob(f"*/{name}")) for clip in load(file.parent, axes)]
