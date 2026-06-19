#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from make_model.model import HashRidgeSaturationModel


def main() -> None:
    parser = argparse.ArgumentParser(description="Predict semantic saturation for one text.")
    parser.add_argument("text")
    parser.add_argument(
        "--model",
        default=Path("make-model/artifacts/saturation-model.json"),
        type=Path,
    )
    parser.add_argument("--final", action="store_true")
    args = parser.parse_args()

    model = HashRidgeSaturationModel.load(args.model)
    print(f"SATURATION={model.predict(args.text, is_final=args.final):.4f}")


if __name__ == "__main__":
    main()
