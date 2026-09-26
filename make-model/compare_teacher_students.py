#!/usr/bin/env python3
"""Offline paired teacher/student comparison; never writes runtime model artifacts."""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from make_model.model import HashRidgeConfig, HashRidgeSaturationModel, fit_hash_ridge_model
from make_model.schema import TeacherLabel, read_jsonl, write_jsonl

TEACHERS = ("gemma", "codex")
DEFAULT_BASELINE = Path(__file__).resolve().parent / "artifacts" / (
    "public-synthetic-gemma26b-200-plus-anchors-life-h8192-l001-saturation-model.json"
)


def index_rows(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(path):
        key = row.get("id")
        if not isinstance(key, str) or not key:
            raise ValueError(f"{path}: nonempty string ID required")
        if key in result:
            raise ValueError(f"{path}: duplicate ID {key}")
        result[key] = row
    return result


def load_comparison(root: Path) -> dict[str, Any]:
    inputs = index_rows(root / "corpus/inputs.jsonl")
    reference = index_rows(root / "corpus/reference.jsonl")
    labels = {name: index_rows(root / f"labels/{name}.jsonl") for name in TEACHERS}
    if not inputs:
        raise ValueError("inputs must not be empty")
    for name, rows in [("reference", reference), *labels.items()]:
        if rows.keys() != inputs.keys():
            raise ValueError(f"{name}: IDs do not exactly match inputs")
    groups: dict[str, set[str]] = {"train": set(), "eval": set()}
    texts: dict[str, set[str]] = {"train": set(), "eval": set()}
    for key, row in inputs.items():
        split, group, text = row.get("split"), row.get("group_id"), row.get("text")
        if split not in groups or not isinstance(group, str) or not group:
            raise ValueError(f"{key}: split train/eval and nonempty group_id required")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{key}: nonempty text required")
        groups[split].add(group)
        texts[split].add("".join(text.split()))
        ref = reference[key]
        if "ready" not in ref and "reference_ready" not in ref:
            raise ValueError(f"{key}: reference ready required")
        ready = ref.get("ready", ref.get("reference_ready"))
        if ready is not None and type(ready) is not bool:
            raise ValueError(f"{key}: reference ready must be bool or null")
        ref["ready"] = ready
        for name in TEACHERS:
            score = labels[name][key].get("score")
            if (type(score) not in (int, float) or not math.isfinite(score)
                    or not 0 <= score <= 1):
                raise ValueError(f"{name}/{key}: score must be finite in [0, 1]")
    for kind, values in [("group", groups), ("text", texts)]:
        if not all(values.values()):
            raise ValueError("both train and eval must be nonempty")
        if values["train"] & values["eval"]:
            raise ValueError(f"train/eval {kind} overlap")
    return {"inputs": inputs, "reference": reference, "labels": labels}


def binary_metrics(ready: list[bool | None], scores: list[float]) -> dict[str, Any]:
    pairs = [(gold, score >= .75) for gold, score in zip(ready, scores, strict=True)
             if gold is not None]
    positive = sum(gold for gold, _ in pairs)
    negative = len(pairs) - positive
    false_start = sum(not gold and pred for gold, pred in pairs)
    false_wait = sum(gold and not pred for gold, pred in pairs)
    fsr = false_start / negative if negative else None
    fwr = false_wait / positive if positive else None
    return {
        "evaluated": len(pairs), "ambiguous_excluded": len(ready) - len(pairs),
        "reference_ready": positive, "reference_wait": negative,
        "false_start_count": false_start, "false_wait_count": false_wait,
        "false_start_rate": fsr, "false_wait_rate": fwr,
        "accuracy": 1 - (false_start + false_wait) / len(pairs) if pairs else None,
        "balanced_accuracy": 1 - (fsr + fwr) / 2
        if fsr is not None and fwr is not None else None,
    }


def timing_stats(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    return {"count": len(values), "mean": statistics.mean(values),
            "median": statistics.median(values),
            "p95": ordered[max(0, math.ceil(len(values) * .95) - 1)],
            "min": min(values), "max": max(values)} if values else {"count": 0}


def predict_latency(model: HashRidgeSaturationModel, texts: list[str],
                    repeats: int) -> dict[str, Any]:
    if repeats < 1:
        raise ValueError("latency repeats must be positive")
    values = []
    for index in range(100 + repeats):
        text = texts[index % len(texts)]
        started = time.perf_counter()
        model.predict(text, is_final=True)
        elapsed = (time.perf_counter() - started) * 1000
        if index >= 100:
            values.append(elapsed)
    return timing_stats(values) | {"warmup": 100, "unit": "ms"}


def run_comparison(root: Path, *, configs: dict[str, HashRidgeConfig] | None = None,
                   baseline_path: Path = DEFAULT_BASELINE,
                   latency_repeats: int = 1000) -> dict[str, Any]:
    data = load_comparison(root)
    inputs, reference, labels = data["inputs"], data["reference"], data["labels"]
    configs = configs or {"primary": HashRidgeConfig(hash_size=2048, ridge_lambda=1.0)}
    splits = {split: [key for key, row in inputs.items() if row["split"] == split]
              for split in ("train", "eval")}
    predictions = {key: {"id": key, "split": row["split"], "text": row["text"],
                        "group_id": row["group_id"], "reference": reference[key],
                        "teachers": {name: labels[name][key]["score"] for name in TEACHERS},
                        "students": {}} for key, row in inputs.items()}
    summary: dict[str, Any] = {
        "reference": {"kind": "predeclared synthetic silver", "human_verified": False,
                      "bias": "Codex-authored reference may favor Codex; not human gold."},
        "protocol": {"threshold": .75, "is_final": True,
                     "is_final_reason": "Constant for all train/eval rows; no corpus-final oracle.",
                     "teacher_input_contract": "Same prompt/text only; enforced by collectors.",
                     "fit_rounds": 3, "fit_order": [list(TEACHERS), list(reversed(TEACHERS)),
                                                    list(TEACHERS)],
                     "counts": {key: len(value) for key, value in splits.items()},
                     "configs": {key: asdict(value) for key, value in configs.items()},
                     "train_policy": "Teacher-only; no manual anchors; eval excluded from fitting.",
                     "timing_note": "Fit excludes file I/O; latency is resident prediction. "
                                    "Teacher time is batch_elapsed / batch_size, not observed "
                                    "per-item latency or total workflow duration.",
                     "tuning": "None; primary and optional secondary configs are predeclared."},
        "teachers": {}, "students": {}, "teacher_agreement": {},
    }

    def metrics(scores: dict[str, float]) -> dict[str, Any]:
        result = {split: binary_metrics([reference[key]["ready"] for key in keys],
                                       [scores[key] for key in keys])
                  for split, keys in splits.items()}
        categories = sorted({reference[key].get("category", "unspecified")
                             for key in splits["eval"]})
        result["eval_by_category"] = {}
        for category in categories:
            keys = [key for key in splits["eval"]
                    if reference[key].get("category", "unspecified") == category]
            result["eval_by_category"][category] = binary_metrics(
                [reference[key]["ready"] for key in keys], [scores[key] for key in keys])
        return result

    for name in TEACHERS:
        scores = {key: row["score"] for key, row in labels[name].items()}
        times = [row["elapsed_ms"] for row in labels[name].values()
                 if type(row.get("elapsed_ms")) in (int, float)
                 and math.isfinite(row["elapsed_ms"]) and row["elapsed_ms"] >= 0]
        summary["teachers"][name] = metrics(scores) | {
            "amortized_label_time_ms": timing_stats(times),
            "timing_note": "Collector divides batch elapsed time by the batch item count; "
                           "this is not observed per-item latency.",
        }
    for split, keys in splits.items():
        summary["teacher_agreement"][split] = {
            "count": len(keys), "score_mae": statistics.mean(
                abs(labels["gemma"][key]["score"] - labels["codex"][key]["score"])
                for key in keys),
            "binary_agreement": statistics.mean(
                (labels["gemma"][key]["score"] >= .75) == (labels["codex"][key]["score"] >= .75)
                for key in keys),
            "disagreement_ids": [key for key in keys
                                 if (labels["gemma"][key]["score"] >= .75)
                                 != (labels["codex"][key]["score"] >= .75)],
        }
    for config_name, config in configs.items():
        fitted, durations = {}, {name: [] for name in TEACHERS}
        train = {name: [TeacherLabel(
            utterance_id=key, prefix_index=0, prefix_text=inputs[key]["text"],
            full_text=inputs[key]["text"], saturation=labels[name][key]["score"],
            teacher_model=name, source="synthetic_teacher_comparison", is_final=True,
        ) for key in splits["train"]] for name in TEACHERS}
        for order in (TEACHERS, tuple(reversed(TEACHERS)), TEACHERS):
            for name in order:
                started = time.perf_counter()
                fitted[name] = fit_hash_ridge_model(train[name], config, metadata={
                    "teacher": name, "train_count": len(train[name]), "constant_is_final": True,
                    "train_ids": splits["train"], "reference_used_for_training": False,
                })
                durations[name].append((time.perf_counter() - started) * 1000)
        for name, model in fitted.items():
            key = f"{config_name}/{name}"
            model.save(root / "models" / f"{config_name}-{name}.json")
            scores = {item: model.predict(row["text"], is_final=True)
                      for item, row in inputs.items()}
            for item, score in scores.items():
                predictions[item]["students"][key] = score
            summary["students"][key] = metrics(scores) | {
                "fit_times_ms": durations[name], "fit_summary_ms": timing_stats(durations[name]),
                "predict_latency_ms": predict_latency(
                    model, [inputs[item]["text"] for item in splits["eval"]], latency_repeats),
            }
    if baseline_path.is_file():
        baseline = HashRidgeSaturationModel.load(baseline_path)
        scores = {key: baseline.predict(row["text"], is_final=True) for key, row in inputs.items()}
        summary["existing_runtime_baseline"] = metrics(scores) | {
            "path": str(baseline_path), "paired_teacher_comparison": False,
            "note": "Different training data, size and anchors; descriptive baseline only. "
                    "Raw scorer only, without runtime short-ack clamping.",
            "metadata": baseline.metadata,
            "predict_latency_ms": predict_latency(
                baseline, [inputs[key]["text"] for key in splits["eval"]], latency_repeats),
        }
        for key, score in scores.items():
            predictions[key]["existing_runtime_baseline"] = score
    else:
        summary["existing_runtime_baseline"] = {"status": "unavailable", "path": str(baseline_path)}
    (root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    write_jsonl(root / "predictions.jsonl", predictions.values())
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--include-runtime-config", action="store_true",
                        help="Also fit 8192/lambda .01 (larger dense normal equations).")
    args = parser.parse_args()
    configs = {"primary": HashRidgeConfig(hash_size=2048, ridge_lambda=1.0)}
    if args.include_runtime_config:
        configs["runtime_config"] = HashRidgeConfig(hash_size=8192, ridge_lambda=.01)
    summary = run_comparison(args.root, configs=configs)
    print(json.dumps({"counts": summary["protocol"]["counts"],
                      "summary": str(args.root / "summary.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
