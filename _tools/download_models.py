from __future__ import annotations

import argparse
from dataclasses import dataclass

from huggingface_hub import snapshot_download


@dataclass(frozen=True)
class ModelDownload:
    repo_id: str
    license_name: str
    note: str
    optional: bool = False


PERMISSIVE_MODELS = [
    ModelDownload("BAAI/bge-m3", "MIT", "embedding backend"),
    ModelDownload("mlx-community/whisper-small-mlx", "MIT", "default STT"),
    ModelDownload("mlx-community/Kokoro-82M-bf16", "Apache-2.0", "default TTS"),
    ModelDownload("mlx-community/gemma-4-e2b-it-4bit", "Apache-2.0", "fallback conversation"),
    ModelDownload("mlx-community/Irodori-TTS-500M-v3-8bit", "MIT", "TTS evaluation"),
    ModelDownload("mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit", "Apache-2.0", "TTS evaluation"),
]

OPTIONAL_MODELS = [
    ModelDownload(
        "lmstudio-community/LFM2.5-1.2B-Instruct-MLX-4bit",
        "lfm1.0",
        "current local conversation backend; custom model license",
        optional=True,
    ),
    ModelDownload(
        "FluidInference/supertonic-3-coreml",
        "openrail++",
        "Supertonic CoreML TTS smoke model; OpenRAIL-family license",
        optional=True,
    ),
    ModelDownload(
        "Reza2kn/supertonic-3-coreml",
        "OpenRAIL",
        "Supertonic female voice styles used for smoke samples",
        optional=True,
    ),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--include-optional",
        action="store_true",
        help="also download custom/OpenRAIL licensed optional model assets",
    )
    args = parser.parse_args()

    targets = list(PERMISSIVE_MODELS)
    if args.include_optional:
        targets.extend(OPTIONAL_MODELS)

    for target in targets:
        tag = "optional" if target.optional else "default"
        print(
            f"download {target.repo_id} "
            f"license={target.license_name} kind={tag} note={target.note}",
            flush=True,
        )
        snapshot_download(target.repo_id)


if __name__ == "__main__":
    main()
