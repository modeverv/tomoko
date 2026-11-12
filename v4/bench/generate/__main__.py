"""台本から入力音声と注釈を作る。

    python -m bench.generate scenarios/small work/inputs/small
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from bench import definitions as d
from bench.audio import write_wav
from bench.generate.tts import (
    Synthesizer,
    annotate_one_pass,
    pick_voice,
    synthesize,
    synthesize_one_pass,
)
from bench.scenario import Manifest, Scenario, StepAudio, load_scenarios


def build(
    scenario: Scenario, out: Path, cache: Path, synth: Synthesizer | None = None,
    *, one_pass: bool = False,
) -> Manifest:
    voice = scenario.voice or pick_voice(scenario.id)
    audio: list[StepAudio] = []
    for i, step in enumerate(scenario.steps):
        if one_pass:
            parts = step.parts()
            samples = synthesize_one_pass(parts, voice, cache, synth=synth)
            voiced, pauses = annotate_one_pass(
                samples, [p for p in parts if isinstance(p, float)],
                label=f"{scenario.id} step {i + 1}",
            )
            t = len(samples) / d.IN_SR
        else:
            synth_fn = synth if synth is not None else synthesize
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
                    s = synth_fn(part, voice, cache)
                    chunks.append(s)
                    voiced.append([round(t, 4), round(t + len(s) / d.IN_SR, 4)])
                t += len(chunks[-1]) / d.IN_SR
            samples = np.concatenate(chunks)
        name = f"step{i}.wav"
        write_wav(out / name, samples, d.IN_SR)
        audio.append(StepAudio(name, round(t, 4), voiced, pauses))
    manifest = Manifest(Scenario(scenario.id, scenario.axis, scenario.steps, scenario.expect, voice),
                        tuple(audio), one_pass=one_pass)
    manifest.save(out)
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--cache", type=Path, default=Path("work/tts-cache"))
    ap.add_argument("--one-pass", action="store_true", help="区切りの無音を埋め、1 発話を 1 回で合成する")
    args = ap.parse_args()
    scenarios = load_scenarios(args.scenarios)
    for s in scenarios:
        build(s, args.out / s.id, args.cache, one_pass=args.one_pass)
    print(f"{len(scenarios)} 件を {args.out} に作った")
    return 0


if __name__ == "__main__":
    sys.exit(main())
