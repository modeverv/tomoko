from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "make-model"))
spec = importlib.util.spec_from_file_location(
    "analyze_feature_learning",
    ROOT / "make-model/analyze_feature_learning.py",
)
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)
from make_model.model import HashRidgeConfig, HashRidgeSaturationModel  # noqa: E402

pytestmark = pytest.mark.unit


def test_group_bootstrap_retains_the_pair_as_sampling_unit() -> None:
    rows = [
        {"group_id": "a", "ready": True, "base": 0.1, "candidate": 0.9},
        {"group_id": "a", "ready": False, "base": 0.1, "candidate": 0.1},
        {"group_id": "b", "ready": True, "base": 0.9, "candidate": 0.9},
        {"group_id": "b", "ready": False, "base": 0.9, "candidate": 0.1},
        {"group_id": "c", "ready": None, "base": 0.9, "candidate": 0.1},
    ]
    result = diagnostics.group_delta(rows, "base", "candidate", repeats=200)
    assert result["group_count"] == 2
    assert result["evaluated_count"] == 4
    assert result["delta_percentage_points"] == 50.0
    assert result["bootstrap_95_percentile_pp"] == [50.0, 50.0]


def test_contributions_reconstruct_the_unclipped_score() -> None:
    config = HashRidgeConfig(hash_size=8)
    weights = np.arange(13, dtype=float) / 20
    model = HashRidgeSaturationModel(config, weights.tolist(), 0.3, {})
    result = diagnostics.contributions(model, "雨ですが、何時ですか")
    total = result["bias"] + result["characters"] + sum(result["extra"].values())
    assert total == pytest.approx(result["raw_score"])
    assert np.clip(total, 0, 1) == pytest.approx(
        model.predict("雨ですが、何時ですか", is_final=True)
    )


def test_normalized_text_detects_punctuation_and_width_copies() -> None:
    assert diagnostics.normalize_text(" Ａ案、どうですか？ ") == diagnostics.normalize_text(
        "A案どうですか"
    )


def test_length_profile_separates_ambiguous_examples() -> None:
    rows = [
        {"split": "train", "text": "あ い", "reference": {"ready": True}},
        {"split": "train", "text": "あ", "reference": {"ready": False}},
        {"split": "train", "text": "あいうえお", "reference": {"ready": None}},
    ]
    profile = diagnostics.length_profile(rows)["train"]
    assert profile["ready"] == {"count": 1, "mean_characters": 2.0}
    assert profile["wait"] == {"count": 1, "mean_characters": 1.0}


def test_overlap_excludes_within_group_pairs_but_finds_cross_split_copy() -> None:
    rows = [
        {"id": "a", "group_id": "g1", "text": "先に窓を閉めてください", "split": "train"},
        {"id": "b", "group_id": "g1", "text": "先に窓を閉めて", "split": "train"},
        {"id": "c", "group_id": "g2", "text": "先に窓を閉めてください。", "split": "blind"},
    ]
    result = diagnostics.overlap_audit(rows)
    assert result["normalized_cross_split_duplicates"] == [["a", "c"]]
    assert result["near_cross_split_pairs"][0]["ids"] == ["a", "c"]
