"""Offline diagnostics for the frozen feature-learning experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
from make_model.model import (
    EXTRA_FEATURES,
    HashRidgeConfig,
    HashRidgeSaturationModel,
    hashed_features,
)


def normalize_text(text: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFKC", text)
        if not c.isspace() and not unicodedata.category(c).startswith("P")
    )


def grams(text: str) -> set[str]:
    text = "".join(text.split())
    return {text[i : i + n] for n in range(1, 5) for i in range(len(text) - n + 1)}


def overlap_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Audit only across train/development/blind, retaining related pairs in each."""
    duplicates, near = [], []
    prepared = []
    for row in rows:
        normalized = normalize_text(row["text"])
        scope = row["split"] if row["split"] in {"development", "blind"} else "train"
        prepared.append((row, normalized, grams(normalized), scope))
    for (a, ta, ga, sa), (b, tb, gb, sb) in combinations(prepared, 2):
        if sa == sb or a["group_id"] == b["group_id"]:
            continue
        if ta == tb:
            duplicates.append([a["id"], b["id"]])
        if len(ga & gb) / max(1, len(ga | gb)) < 0.4:
            continue
        ratio = SequenceMatcher(None, ta, tb, autojunk=False).ratio()
        if ratio >= 0.82:
            near.append({"ids": [a["id"], b["id"]], "ratio": ratio, "scopes": [sa, sb]})
    return {
        "normalized_cross_split_duplicates": duplicates,
        "near_cross_split_pair_count": len(near),
        "near_cross_split_pairs": sorted(near, key=lambda r: -r["ratio"])[:50],
        "near_filter": "1-4gram Jaccard>=0.4 then SequenceMatcher>=0.82; not semantic dedup",
    }


def group_delta(
    rows: list[dict[str, Any]],
    base: str,
    candidate: str,
    *,
    repeats: int = 2000,
) -> dict[str, Any]:
    groups: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        if row["ready"] is None:
            continue
        improvement = int((row[candidate] >= 0.75) == row["ready"])
        improvement -= int((row[base] >= 0.75) == row["ready"])
        groups[row["group_id"]][0] += improvement
        groups[row["group_id"]][1] += 1
    if not groups:
        raise ValueError("no unambiguous groups")
    values = np.array(list(groups.values()), dtype=float)
    rng = np.random.default_rng(20260927)
    sampled = values[rng.integers(0, len(values), size=(repeats, len(values)))].sum(axis=1)
    changes = sampled[:, 0] / sampled[:, 1] * 100
    return {
        "group_count": len(values),
        "evaluated_count": int(values[:, 1].sum()),
        "delta_percentage_points": float(values[:, 0].sum() / values[:, 1].sum() * 100),
        "bootstrap_95_percentile_pp": np.percentile(changes, [2.5, 97.5]).tolist(),
        "repeats": repeats,
        "seed": 20260927,
        "note": "Group bootstrap on this artificial corpus, not real-conversation uncertainty.",
    }


def contributions(model: HashRidgeSaturationModel, text: str) -> dict[str, Any]:
    x = hashed_features(text, model.config, is_final=True)
    terms = x * np.asarray(model.weights)
    size = model.config.hash_size
    return {
        "bias": model.bias,
        "characters": float(terms[:size].sum()),
        "extra": dict(zip(EXTRA_FEATURES, terms[size:].tolist(), strict=True)),
        "raw_score": float(terms.sum() + model.bias),
    }


def length_profile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        ready = row["reference"]["ready"]
        if ready is not None:
            groups[row["split"]]["ready" if ready else "wait"].append(
                len("".join(row["text"].split()))
            )
    return {
        split: {
            key: {"count": len(values), "mean_characters": float(np.mean(values))}
            for key, values in classes.items()
        }
        for split, classes in groups.items()
    }


def representation_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    config = HashRidgeConfig(hash_size=8192, ridge_lambda=0.01)
    vectors = {r["id"]: hashed_features(r["text"], config, is_final=True) for r in rows}
    signatures: dict[bytes, list[dict[str, Any]]] = defaultdict(list)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        signatures[vectors[row["id"]].tobytes()].append(row)
        groups[row["group_id"]].append(row)
    collisions = [
        [r["id"] for r in group]
        for group in signatures.values()
        if {r["reference"]["ready"] for r in group} >= {True, False}
    ]
    similarities = defaultdict(list)
    for group in groups.values():
        for a, b in combinations(group, 2):
            if {a["reference"]["ready"], b["reference"]["ready"]} != {True, False}:
                continue
            xa, xb = vectors[a["id"]][:8192], vectors[b["id"]][:8192]
            cosine = float(np.dot(xa, xb) / max(1e-12, np.linalg.norm(xa) * np.linalg.norm(xb)))
            similarities[a["split"]].append(cosine)
    return {
        "opposite_reference_exact_feature_collisions": collisions,
        "pair_character_cosine": {
            k: {"count": len(v), "mean": float(np.mean(v)), "min": min(v), "max": max(v)}
            for k, v in similarities.items()
        },
        "note": "Distinct vectors do not prove linear separability or semantic generalization.",
    }


def analyze(results: Path) -> dict[str, Any]:
    rows = [json.loads(line) for line in (results / "predictions.jsonl").read_text().splitlines()]
    manifest = json.loads((results / "conditions.json").read_text())
    conditions = manifest["conditions"]
    by_id = {r["id"]: r for r in rows}
    blind = [r for r in rows if r["split"] == "blind"]
    flat = [
        {"group_id": r["group_id"], "ready": r["reference"]["ready"], **r["scores"]} for r in blind
    ]
    comparisons = [("baseline", name) for name in conditions if name != "baseline"]
    comparisons += [("general320", "targeted320"), ("combined640_char_only", "combined640")]
    result = {
        "overlap": overlap_audit(rows),
        "representation": representation_audit(rows),
        "blind_paired_deltas": {f"{a} -> {b}": group_delta(flat, a, b) for a, b in comparisons},
        "old_development_errors": [],
        "gram_coverage": {},
        "contributions": [],
        "length_profile": length_profile(rows),
        "feature_weights": {},
        "length_analysis_note": "Post-result descriptive diagnostic, not a length-only ablation.",
    }
    for row in rows:
        ready = row["reference"]["ready"]
        if row["split"] == "development" and ready is not None:
            if (row["scores"]["baseline"] >= 0.75) != ready:
                result["old_development_errors"].append(
                    {"id": row["id"], "text": row["text"], "ready": ready, "scores": row["scores"]}
                )
    for name in ("baseline", "combined640"):
        training = [by_id[key] for key in conditions[name]["train_ids"]]
        vocabulary = set().union(*(grams(r["text"]) for r in training))
        coverage = [
            len(grams(r["text"]) & vocabulary) / max(1, len(grams(r["text"]))) for r in blind
        ]
        result["gram_coverage"][name] = {
            "training_unique_1_to_4_grams": len(vocabulary),
            "blind_mean_gram_coverage": float(np.mean(coverage)),
        }
        model = HashRidgeSaturationModel.load(results / conditions[name]["model_path"])
        result["feature_weights"][name] = {
            "bias": model.bias,
            "extra": dict(zip(EXTRA_FEATURES, model.weights[-len(EXTRA_FEATURES) :], strict=True)),
        }
        for row in rows:
            if row["split"] in {"development", "blind"}:
                result["contributions"].append(
                    {"id": row["id"], "condition": name, **contributions(model, row["text"])}
                )
    result["input_sha256"] = {
        name: hashlib.sha256((results / name).read_bytes()).hexdigest()
        for name in ("predictions.jsonl", "conditions.json")
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.results)
    (args.results / "diagnostics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in {"contributions", "old_development_errors"}
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
