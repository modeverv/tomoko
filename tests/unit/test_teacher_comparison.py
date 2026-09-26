from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "make-model"))

from compare_teacher_students import (  # noqa: E402
    binary_metrics,
    load_comparison,
    run_comparison,
)
from make_model.model import HashRidgeConfig  # noqa: E402

pytestmark = pytest.mark.unit


def write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


@pytest.fixture
def comparison_root(tmp_path: Path) -> Path:
    rows = [
        {"id": "t1", "text": "音量を下げて", "split": "train", "group_id": "tr1"},
        {"id": "t2", "text": "明日の", "split": "train", "group_id": "tr2"},
        {"id": "e1", "text": "予定はありますか", "split": "eval", "group_id": "ev1"},
        {"id": "e2", "text": "そういえば昨日の", "split": "eval", "group_id": "ev2"},
        {"id": "e3", "text": "三時", "split": "eval", "group_id": "ev3"},
    ]
    write_rows(tmp_path / "corpus/inputs.jsonl", rows)
    write_rows(tmp_path / "corpus/reference.jsonl", [
        {"id": row["id"], "ready": ready, "category": "test", "reason": "synthetic"}
        for row, ready in zip(rows, [True, False, True, False, None], strict=True)
    ])
    for teacher, scores in {"gemma": [.95, .1, .9, .8, .5],
                            "codex": [.9, .2, .85, .2, .4]}.items():
        write_rows(tmp_path / f"labels/{teacher}.jsonl", [
            {"id": row["id"], "score": score, "elapsed_ms": 2.0}
            for row, score in zip(rows, scores, strict=True)
        ])
    return tmp_path


@pytest.mark.parametrize("field,value", [("group_id", "tr1"), ("text", "音量を 下げて")])
def test_rejects_cross_split_leakage(comparison_root: Path, field: str, value: str) -> None:
    path = comparison_root / "corpus/inputs.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[2][field] = value
    write_rows(path, rows)
    with pytest.raises(ValueError, match="overlap"):
        load_comparison(comparison_root)


@pytest.mark.parametrize("bad_score", [float("nan"), float("inf"), -0.1, 1.1, True])
def test_rejects_invalid_teacher_scores(comparison_root: Path, bad_score: float) -> None:
    path = comparison_root / "labels/gemma.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]["score"] = bad_score
    write_rows(path, rows)
    with pytest.raises(ValueError, match="score"):
        load_comparison(comparison_root)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "extra"])
def test_requires_exact_teacher_ids(comparison_root: Path, mutation: str) -> None:
    path = comparison_root / "labels/codex.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if mutation == "missing":
        rows.pop()
    else:
        rows.append(rows[0] | ({"id": "unknown"} if mutation == "extra" else {}))
    write_rows(path, rows)
    with pytest.raises(ValueError, match="IDs|duplicate"):
        load_comparison(comparison_root)


def test_metrics_exclude_ambiguous_and_use_class_denominators() -> None:
    result = binary_metrics([True, True, False, False, None], [.9, .2, .8, .1, .99])
    assert result["evaluated"] == 4
    assert result["ambiguous_excluded"] == 1
    assert result["false_start_count"] == result["false_wait_count"] == 1
    assert result["false_start_rate"] == result["false_wait_rate"] == .5
    assert result["balanced_accuracy"] == .5
    assert binary_metrics([None], [.9])["accuracy"] is None


def test_end_to_end_saves_isolated_models_and_preserves_reference(comparison_root: Path) -> None:
    reference = (comparison_root / "corpus/reference.jsonl").read_bytes()
    result = run_comparison(
        comparison_root, configs={"primary": HashRidgeConfig(hash_size=16)},
        baseline_path=comparison_root / "missing-baseline.json", latency_repeats=12,
    )
    assert result["reference"]["human_verified"] is False
    assert result["protocol"]["is_final"] is True
    assert result["teachers"]["gemma"]["eval"]["false_start_count"] == 1
    assert result["teachers"]["codex"]["eval"]["false_start_count"] == 0
    for teacher in ("gemma", "codex"):
        student = result["students"][f"primary/{teacher}"]
        assert len(student["fit_times_ms"]) == 3
        assert student["predict_latency_ms"]["count"] == 12
        model_path = comparison_root / "models" / f"primary-{teacher}.json"
        artifact = json.loads(model_path.read_text())
        assert artifact["metadata"]["train_count"] == 2
        assert artifact["metadata"]["constant_is_final"] is True
    assert (comparison_root / "summary.json").is_file()
    assert len((comparison_root / "predictions.jsonl").read_text().splitlines()) == 5
    assert (comparison_root / "corpus/reference.jsonl").read_bytes() == reference
