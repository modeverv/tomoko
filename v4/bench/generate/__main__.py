"""台本から入力音声と注釈を作る。

    python -m bench.generate scenarios/small work/inputs/small
"""

import argparse
import sys
from pathlib import Path

import numpy as np

from bench import definitions as d
from bench.audio import write_wav
from bench.generate.tts import pick_voice, synthesize
from bench.scenario import Manifest, Scenario, StepAudio, load_scenarios


def build(scenario: Scenario, out: Path, cache: Path, synth=synthesize) -> Manifest:
    voice = scenario.voice or pick_voice(scenario.id)
    audio: list[StepAudio] = []
    for i, step in enumerate(scenario.steps):
        chunks: list[np.ndarray] = []
        voiced: list[list[float]] = []
        pauses: list[list[float]] = []
        t = 0.0
        for part in step.parts():
            if isinstance(part, float):
                n = round(part * d.IN_SR)
                chunks.append(np.zeros(n, dtype=np.float32))
                pauses.append([round(t, 4), round(t + n / d.IN_SR, 4)])
            else:
                s = synth(part, voice, cache)
                chunks.append(s)
                voiced.append([round(t, 4), round(t + len(s) / d.IN_SR, 4)])
            t += len(chunks[-1]) / d.IN_SR
        name = f"step{i}.wav"
        write_wav(out / name, np.concatenate(chunks), d.IN_SR)
        audio.append(StepAudio(name, round(t, 4), voiced, pauses))
    manifest = Manifest(Scenario(scenario.id, scenario.axis, scenario.steps, scenario.expect, voice),
                        tuple(audio))
    manifest.save(out)
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--cache", type=Path, default=Path("work/tts-cache"))
    args = ap.parse_args()
    scenarios = load_scenarios(args.scenarios)
    for s in scenarios:
        build(s, args.out / s.id, args.cache)
    print(f"{len(scenarios)} 件を {args.out} に作った")
    return 0


if __name__ == "__main__":
    sys.exit(main())
