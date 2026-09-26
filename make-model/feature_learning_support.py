"""Data isolation and fixed-policy metrics for the offline feature learning probe."""
from __future__ import annotations

import math
import statistics
import unicodedata
from collections import defaultdict
from itertools import combinations, product
from pathlib import Path
from typing import Any

from compare_teacher_students import index_rows


def normalized_text(text: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKC", text)
                   if not char.isspace() and not unicodedata.category(char).startswith("P"))


def load_dataset(directory: Path, *, labeled: bool) -> list[dict[str, Any]]:
    inputs = index_rows(directory / "corpus/inputs.jsonl")
    refs = index_rows(directory / "corpus/reference.jsonl")
    labels = index_rows(directory / "labels/codex.jsonl") if labeled else None
    if not inputs or refs.keys() != inputs.keys():
        raise ValueError(f"{directory}: reference IDs must exactly match nonempty inputs")
    if labels is not None and labels.keys() != inputs.keys():
        raise ValueError(f"{directory}: teacher IDs must exactly match inputs")
    rows = []
    for key, original in inputs.items():
        row, ref = dict(original), dict(refs[key])
        if not isinstance(row.get("text"), str) or not normalized_text(row["text"]):
            raise ValueError(f"{key}: nonempty normalized text required")
        if not isinstance(row.get("group_id"), str) or not row["group_id"]:
            raise ValueError(f"{key}: nonempty group_id required")
        if "ready" not in ref and "reference_ready" not in ref:
            raise ValueError(f"{key}: reference readiness required")
        ready = ref.get("ready", ref.get("reference_ready"))
        if ready is not None and type(ready) is not bool:
            raise ValueError(f"{key}: reference readiness must be bool or null")
        ref["ready"] = ready
        score = labels[key].get("score") if labels is not None else None
        if labeled and (type(score) not in (int, float) or not math.isfinite(score)
                        or not 0 <= score <= 1):
            raise ValueError(f"{key}: teacher score must be finite and in [0, 1]")
        rows.append(row | {"reference": ref, "teacher_score": score,
                           "arm": ref.get("arm", row.get("arm")),
                           "acquisition_order": ref.get("acquisition_order")})
    return rows


def load_experiment(root: Path, base_root: Path) -> dict[str, list[dict[str, Any]]]:
    old = load_dataset(base_root, labeled=True)
    additions = load_dataset(root / "train-additions", labeled=True)
    blind = load_dataset(root / "blind", labeled=False)
    if any(row.get("split") not in ("train", "eval") for row in old):
        raise ValueError("base source split must be train or eval")
    if any(row.get("arm") not in ("targeted", "general") for row in additions):
        raise ValueError("additional reference arm must be targeted or general")
    if any(row.get("split", "train") != "train" for row in additions):
        raise ValueError("additional inputs must be marked train")
    parts = {"base": [row for row in old if row["split"] == "train"],
             "development": [row for row in old if row["split"] == "eval"],
             "targeted": [row for row in additions if row["arm"] == "targeted"],
             "general": [row for row in additions if row["arm"] == "general"], "blind": blind}
    for name, rows in parts.items():
        if not rows:
            raise ValueError(f"empty partition {name}")
        for row in rows:
            row["split"] = name
    for left, right in combinations(parts, 2):
        for field in ("id", "group_id", "text"):
            normalize = normalized_text if field == "text" else str
            a = {normalize(row[field]) for row in parts[left]}
            b = {normalize(row[field]) for row in parts[right]}
            if a & b:
                raise ValueError(f"{left}/{right}: {field} overlap")
    return parts


def select_group_prefix(rows: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["group_id"]].append(row)
    fallback = {key: i for i, key in enumerate(groups)}

    def order(key: str) -> float:
        values = [row.get("acquisition_order") for row in groups[key]]
        if any(value is not None for value in values):
            if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
                raise ValueError("acquisition_order must be finite for every group member")
            return min(values)
        return fallback[key]

    selected = []
    for key in sorted(groups, key=order):
        if len(selected) == count:
            break
        if len(selected) + len(groups[key]) > count:
            raise ValueError(f"requested {count} rows would split a group")
        selected.extend(groups[key])
    if len(selected) != count:
        raise ValueError(f"requested {count} examples; got {len(selected)}")
    return selected


def build_conditions(parts: dict[str, list[dict[str, Any]]], sizes: tuple[int, ...]
                     ) -> dict[str, tuple[list[dict[str, Any]], bool]]:
    maximum = max(sizes)
    if len(parts["targeted"]) != maximum or len(parts["general"]) != maximum:
        raise ValueError("targeted and general counts must equal largest fixed subset")
    base = parts["base"]
    full = {"baseline": (base, False)}
    for size in sizes:
        full[f"targeted{size}"] = (base + select_group_prefix(parts["targeted"], size), False)
    full[f"general{maximum}"] = (base + select_group_prefix(parts["general"], maximum), False)
    combined = base + parts["targeted"] + parts["general"]
    full[f"combined{2 * maximum}"] = (combined, False)
    full["baseline_char_only"] = (base, True)
    full[f"combined{2 * maximum}_char_only"] = (combined, True)
    return full


def readiness(row: dict[str, Any]) -> bool | None:
    return row.get("reference", row).get("ready")


def binary_metrics_at(rows: list[dict[str, Any]], scores: dict[str, float],
                      threshold: float) -> dict[str, Any]:
    pairs = [(readiness(row), scores[row["id"]] >= threshold) for row in rows
             if readiness(row) is not None]
    positive = sum(gold for gold, _ in pairs)
    negative = len(pairs) - positive
    false_start = sum(not gold and predicted for gold, predicted in pairs)
    false_wait = sum(gold and not predicted for gold, predicted in pairs)
    fsr = false_start / negative if negative else None
    fwr = false_wait / positive if positive else None
    return {"evaluated": len(pairs), "ambiguous_excluded": len(rows) - len(pairs),
            "reference_ready": positive, "reference_wait": negative,
            "false_start_count": false_start, "false_wait_count": false_wait,
            "false_start_rate": fsr, "false_wait_rate": fwr,
            "accuracy": 1 - (false_start + false_wait) / len(pairs) if pairs else None,
            "balanced_accuracy": 1 - (fsr + fwr) / 2
            if fsr is not None and fwr is not None else None}


def metric_bundle(rows: list[dict[str, Any]], scores: dict[str, float], *,
                  threshold: float = .75) -> dict[str, Any]:
    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        categories[row.get("reference", row).get("category", "unspecified")].append(row)
        groups[row["group_id"]].append(row)
    margins = []
    for group in groups.values():
        ready = [row for row in group if readiness(row) is True]
        wait = [row for row in group if readiness(row) is False]
        margins.extend(scores[a["id"]] - scores[b["id"]] for a, b in product(ready, wait))
    return {"threshold": threshold, "overall": binary_metrics_at(rows, scores, threshold),
            "by_category": {key: binary_metrics_at(group, scores, threshold)
                            for key, group in categories.items()},
            "pair_ranking": {"comparable_pairs": len(margins), "groups": len(groups),
                             "wins": sum(margin > 0 for margin in margins),
                             "ties": sum(margin == 0 for margin in margins),
                             "losses": sum(margin < 0 for margin in margins),
                             "strict_order_accuracy":
                             statistics.mean(margin > 0 for margin in margins) if margins else None,
                             "mean_ready_minus_wait":
                             statistics.mean(margins) if margins else None}}


def choose_dev_threshold(rows: list[dict[str, Any]], scores: dict[str, float]) -> float:
    candidates = [value / 100 for value in range(5, 96)]

    def rank(threshold: float) -> tuple[float, float, float]:
        accuracy = binary_metrics_at(rows, scores, threshold)["accuracy"]
        return (-1 if accuracy is None else accuracy, -round(abs(threshold - .75), 8), threshold)

    return max(candidates, key=rank)
