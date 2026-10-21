#!/usr/bin/env python3
"""Reproduce the fixed offline data-addition and numeric-feature ablation experiment."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
from compare_teacher_students import predict_latency, timing_stats
from feature_learning_support import (
    build_conditions,
    choose_dev_threshold,
    load_experiment,
    metric_bundle,
)
from make_model.model import (
    EXTRA_FEATURES,
    HashRidgeConfig,
    HashRidgeSaturationModel,
    fit_hash_ridge_model,
    hashed_features,
)
from make_model.schema import TeacherLabel, write_jsonl


def fit_probe(labels: list[TeacherLabel], config: HashRidgeConfig, *,
              char_only: bool) -> HashRidgeSaturationModel:
    """Fit only explicit teacher DTOs; evaluation/reference objects are not accepted."""
    if not char_only:
        return fit_hash_ridge_model(labels, config)
    if not labels:
        raise ValueError("training labels required")
    matrix = np.vstack([hashed_features(label.prefix_text, config, is_final=True)
                        for label in labels])
    matrix[:, config.hash_size:] = 0
    target = np.array([label.saturation for label in labels], dtype=np.float64)
    design = np.hstack([matrix, np.ones((len(labels), 1), dtype=np.float64)])
    regularizer = config.ridge_lambda * np.eye(design.shape[1], dtype=np.float64)
    regularizer[-1, -1] = 0
    lhs, rhs = design.T @ design + regularizer, design.T @ target
    try:
        solution = np.linalg.solve(lhs, rhs)
    except np.linalg.LinAlgError:
        solution = np.linalg.pinv(lhs) @ rhs
    if not np.allclose(solution[config.hash_size:-1], 0, atol=1e-12):
        raise ValueError("numeric feature weights must be zero in char-only ablation")
    solution[config.hash_size:-1] = 0
    return HashRidgeSaturationModel(config, solution[:-1].tolist(), float(solution[-1]), {})


def teacher_labels(rows: list[dict[str, Any]]) -> list[TeacherLabel]:
    return [TeacherLabel(
        utterance_id=row["id"], prefix_index=0, prefix_text=row["text"], full_text=row["text"],
        saturation=row["teacher_score"], teacher_model="codex-gpt-6-astra-medium",
        source="synthetic_feature_learning", is_final=True,
    ) for row in rows]


def teacher_fit_metrics(rows: list[dict[str, Any]], scores: dict[str, float]) -> dict[str, Any]:
    errors = [scores[row["id"]] - row["teacher_score"] for row in rows]
    return {"count": len(rows), "mae": statistics.mean(abs(value) for value in errors),
            "rmse": statistics.mean(value * value for value in errors) ** .5,
            "binary_agreement": statistics.mean(
                (scores[row["id"]] >= .75) == (row["teacher_score"] >= .75) for row in rows)}


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")


def file_hashes(root: Path, base_root: Path) -> dict[str, str]:
    paths = []
    for directory, labeled in ((base_root, True), (root / "train-additions", True),
                               (root / "blind", False)):
        paths.extend(directory / "corpus" / name for name in ("inputs.jsonl", "reference.jsonl"))
        if labeled:
            paths.append(directory / "labels/codex.jsonl")
    paths.extend(path for path in [root / "PROTOCOL.md"] if path.is_file())
    return {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def run_experiment(root: Path, base_root: Path, *, config: HashRidgeConfig | None = None,
                   targeted_sizes: tuple[int, ...] = (80, 160, 320),
                   latency_repeats: int = 1000) -> dict[str, Any]:
    parts = load_experiment(root, base_root)
    conditions = build_conditions(parts, targeted_sizes)
    config = config or HashRidgeConfig(hash_size=8192, ridge_lambda=.01)
    order = list(conditions)
    orders = [order, list(reversed(order)), order]
    out = root / "results"
    out.mkdir(parents=True, exist_ok=True)
    protocol = {
        "config": asdict(config), "fixed_threshold": .75, "is_final": True,
        "reference_used_for_training": False,
        "dev_is_known_from_previous_experiment": True,
        "reference": "Independent synthetic AI expectations, not human gold; shared-family bias.",
        "subset_rule": "Nested complete groups in predeclared acquisition order.",
        "feature_ablation": "Same normalized signed character hash; zero numeric columns.",
        "numeric_features": list(EXTRA_FEATURES), "fit_orders": orders,
        "threshold_diagnostic": "Development accuracy maximum over .05 to .95 step .01; "
                                "ties prefer nearest .75 then higher; never selected on blind.",
        "latency_note": "Resident raw scorer; no runtime short-ack clamp or speech gate.",
        "counts": {key: len(rows) for key, rows in parts.items()},
        "source_sha256": file_hashes(root, base_root),
    }
    manifest: dict[str, Any] = {"protocol": protocol, "conditions": {}}
    labels = {name: teacher_labels(rows) for name, (rows, _) in conditions.items()}
    timings: dict[str, list[float]] = {name: [] for name in conditions}
    fitted = {}
    for round_index, names in enumerate(orders, 1):
        for name in names:
            rows, char_only = conditions[name]
            started = time.perf_counter()
            model = fit_probe(labels[name], config, char_only=char_only)
            elapsed = (time.perf_counter() - started) * 1000
            timings[name].append(elapsed)
            fitted[name] = model
            print(json.dumps({"round": round_index, "condition": name, "fit_ms": elapsed}),
                  flush=True)
    all_rows = [row for rows in parts.values() for row in rows]
    predictions = {row["id"]: {key: row[key] for key in
                              ("id", "text", "group_id", "split", "reference", "teacher_score")}
                   | {"scores": {}} for row in all_rows}
    summary: dict[str, Any] = {"protocol": protocol, "conditions": {}}
    for name, (train, char_only) in conditions.items():
        model = fitted[name]
        model.metadata.update({"train_count": len(train), "train_ids": [row["id"] for row in train],
                               "feature_mode": "char_only" if char_only else "full",
                               "reference_used_for_training": False, "constant_is_final": True})
        model_path = Path("models") / f"{name}.json"
        model.save(out / model_path)
        manifest["conditions"][name] = model.metadata | {"model_path": str(model_path)}
        scores = {row["id"]: model.predict(row["text"], is_final=True) for row in all_rows}
        for key, score in scores.items():
            predictions[key]["scores"][name] = score
        fixed_dev = metric_bundle(parts["development"], scores)
        fixed_blind = metric_bundle(parts["blind"], scores)
        threshold = choose_dev_threshold(parts["development"], scores)
        selected_dev = metric_bundle(parts["development"], scores, threshold=threshold)
        selected_blind = metric_bundle(parts["blind"], scores, threshold=threshold)
        summary["conditions"][name] = {
            "train_count": len(train), "feature_mode": model.metadata["feature_mode"],
            "train_teacher": teacher_fit_metrics(train, scores),
            "train_reference": metric_bundle(train, scores),
            "development": fixed_dev, "blind": fixed_blind,
            "dev_threshold_diagnostic": {
                "selected_threshold": threshold, "development": selected_dev,
                "blind": selected_blind,
                "development_false_start_delta": selected_dev["overall"]["false_start_count"]
                - fixed_dev["overall"]["false_start_count"],
                "blind_false_start_delta": selected_blind["overall"]["false_start_count"]
                - fixed_blind["overall"]["false_start_count"],
            },
            "fit_times_ms": timings[name], "fit_summary_ms": timing_stats(timings[name]),
            "predict_latency_ms": predict_latency(
                model, [row["text"] for row in parts["blind"]], latency_repeats),
        }
    save_json(out / "conditions.json", manifest)
    save_json(out / "summary.json", summary)
    write_jsonl(out / "predictions.jsonl", predictions.values())
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--base-root", type=Path, required=True)
    args = parser.parse_args()
    if not (args.root / "PROTOCOL.md").is_file():
        parser.error("freeze root/PROTOCOL.md before running the experiment")
    run_experiment(args.root, args.base_root)
    print(f"wrote {args.root / 'results/summary.json'}")


if __name__ == "__main__":
    main()
