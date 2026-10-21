#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from make_model.append_dedupe import AppendDedupeInput, HashRidgeAppendDedupeModel


def main() -> None:
    parser = argparse.ArgumentParser(description="Predict append dedupe label for a pair.")
    parser.add_argument("--previous", required=True)
    parser.add_argument("--current", required=True)
    parser.add_argument("--time-delta-ms", default=1000, type=int)
    parser.add_argument("--tomoko-speaking", action="store_true")
    parser.add_argument("--speech-queue-active", action="store_true")
    parser.add_argument("--not-final", action="store_true")
    parser.add_argument(
        "--model",
        default=Path("make-model/artifacts/public-synthetic-append-dedupe-model.json"),
        type=Path,
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    model = HashRidgeAppendDedupeModel.load(args.model)
    result = model.predict(
        AppendDedupeInput(
            previous_user_text=args.previous,
            current_user_text=args.current,
            time_delta_ms=args.time_delta_ms,
            tomoko_speaking=args.tomoko_speaking,
            speech_queue_active=args.speech_queue_active,
            current_is_final=not args.not_final,
        )
    )
    payload = {
        "duplicate_score": result.duplicate_score,
        "continuation_score": result.continuation_score,
        "new_intent_score": result.new_intent_score,
        "label": result.label,
        "features": result.features,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    print(f"LABEL={result.label}")
    print(f"DUPLICATE_SCORE={result.duplicate_score:.4f}")
    print(f"CONTINUATION_SCORE={result.continuation_score:.4f}")
    print(f"NEW_INTENT_SCORE={result.new_intent_score:.4f}")


if __name__ == "__main__":
    main()
