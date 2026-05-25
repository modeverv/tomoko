from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType

import numpy as np
import soundfile as sf
from huggingface_hub import hf_hub_download, snapshot_download

REPO_ID = "FluidInference/supertonic-3-coreml"
VOICE_STYLE_REPO_ID = "Reza2kn/supertonic-3-coreml"
DEFAULT_TEXT = "こんにちは、トモコです。今日は少しだけ話してみます。"


@dataclass(frozen=True, slots=True)
class SupertonicRun:
    run: int
    elapsed_ms: float
    audio_ms: float
    rtfx: float
    rms: float
    peak: float
    output: str


def summarize_runs(runs: list[SupertonicRun]) -> dict[str, float]:
    if not runs:
        raise ValueError("at least one run is required")
    values = [run.elapsed_ms for run in runs]
    return {
        "avg_ms": sum(values) / len(values),
        "min_ms": min(values),
        "max_ms": max(values),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke benchmark Supertonic-3 CoreML TTS with Japanese text.",
    )
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--lang", default="ja")
    parser.add_argument("--voice-style", default="M1")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--total-step", type=int, default=8)
    parser.add_argument("--speed", type=float, default=1.05)
    parser.add_argument("--compute-units", default="CPU_AND_NE")
    parser.add_argument("--model-dir", default="logs/supertonic-coreml-smoke/model")
    parser.add_argument("--output-dir", default="logs/supertonic-coreml-smoke")
    args = parser.parse_args()

    if args.runs < 1:
        raise ValueError("--runs must be >= 1")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    model_dir = prepare_model_dir(Path(args.model_dir))
    infer = load_infer_module(model_dir / "infer.py")
    compute_units = getattr(infer.ct.ComputeUnit, args.compute_units)

    load_start = time.perf_counter()
    tts = infer.Supertonic3TTS(model_dir, compute_units)
    load_ms = (time.perf_counter() - load_start) * 1000

    voice_style_path = model_dir / "voice_styles" / f"{args.voice_style}.json"
    ensure_voice_style(args.voice_style, voice_style_path)
    runs: list[SupertonicRun] = []
    for index in range(args.runs):
        start = time.perf_counter()
        wav, duration = tts.synthesize(
            args.text,
            voice_style_path,
            lang=args.lang,
            total_step=args.total_step,
            speed=args.speed,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        path = output_dir / f"{args.lang}-{args.voice_style}-run{index + 1}.wav"
        sf.write(path, wav, tts.sample_rate)
        audio_ms = duration * 1000
        runs.append(
            SupertonicRun(
                run=index + 1,
                elapsed_ms=elapsed_ms,
                audio_ms=audio_ms,
                rtfx=duration / (elapsed_ms / 1000),
                rms=float(np.sqrt(np.mean(np.square(wav.astype(np.float32))))),
                peak=float(np.max(np.abs(wav))),
                output=str(path),
            )
        )

    payload = {
        "backend": "supertonic-3-coreml",
        "repo_id": REPO_ID,
        "compute_units": args.compute_units,
        "text": args.text,
        "language": args.lang,
        "voice_style": args.voice_style,
        "load_ms": load_ms,
        **summarize_runs(runs),
        "runs": [asdict(run) for run in runs],
    }
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\nJSON: {summary_path}")


def prepare_model_dir(model_dir: Path) -> Path:
    if (model_dir / "infer.py").exists():
        return model_dir

    snapshot = Path(
        snapshot_download(
            REPO_ID,
            allow_patterns=[
                "*.mlpackage/*",
                "tts.json",
                "unicode_indexer.json",
                "voice_styles/M1.json",
                "infer.py",
            ],
        )
    )
    model_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(snapshot, model_dir, symlinks=False, dirs_exist_ok=True)
    return model_dir


def ensure_voice_style(voice_style: str, path: Path) -> None:
    if path.exists():
        return
    source = Path(
        hf_hub_download(VOICE_STYLE_REPO_ID, f"voice_styles/{voice_style}.json")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, path)


def load_infer_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("supertonic_coreml_infer", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Supertonic infer module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    main()
