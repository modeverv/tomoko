from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "make-model"))

from feature_learning_support import (  # noqa: E402
    choose_dev_threshold,
    load_experiment,
    metric_bundle,
    select_group_prefix,
)
from make_model.model import HashRidgeConfig, fit_hash_ridge_model, hashed_features  # noqa: E402
from make_model.schema import TeacherLabel  # noqa: E402
from probe_feature_learning import fit_probe, run_experiment  # noqa: E402

pytestmark = pytest.mark.unit


def write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def sample(root: Path, namespace: str, count: int, *, arm: str | None = None,
           split: str = "train") -> list[dict]:
    rows, refs, labels = [], [], []
    for i in range(count):
        key = f"{namespace}-{i}"
        ready = i % 2 == 0
        text = f"{namespace}{i // 2}の予定" + ("を教えて" if ready else "についてだけど")
        rows.append({"id": key, "text": text, "split": split,
                     "group_id": f"{namespace}-g{i // 2}"})
        refs.append({"id": key, "ready": ready, "category": "request", "arm": arm,
                     "acquisition_order": i // 2})
        labels.append({"id": key, "score": .95 if ready else .1})
    write_rows(root / "corpus/inputs.jsonl", rows)
    write_rows(root / "corpus/reference.jsonl", refs)
    write_rows(root / "labels/codex.jsonl", labels)
    return rows


@pytest.fixture
def experiment(tmp_path: Path) -> tuple[Path, Path]:
    base = tmp_path / "old"
    root = tmp_path / "new"
    sample(base, "base", 4)
    extra = tmp_path / "dev"
    sample(extra, "dev", 4, split="eval")
    for relative in ("corpus/inputs.jsonl", "corpus/reference.jsonl", "labels/codex.jsonl"):
        with (base / relative).open("a") as out:
            out.write((extra / relative).read_text())
    additions = root / "train-additions"
    sample(additions, "targeted", 6, arm="targeted")
    sample(extra, "general", 6, arm="general")
    for relative in ("corpus/inputs.jsonl", "corpus/reference.jsonl", "labels/codex.jsonl"):
        with (additions / relative).open("a") as out:
            out.write((extra / relative).read_text())
    sample(root / "blind", "blind", 4, split="blind")
    return root, base


@pytest.mark.parametrize("field,value", [
    ("id", "base-0"), ("group_id", "base-g0"), ("text", "ｂａｓｅ０の予定、 を教えて。"),
])
def test_experiment_rejects_leakage(experiment: tuple[Path, Path], field: str, value: str) -> None:
    root, base = experiment
    path = root / "blind/corpus/inputs.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if field == "id":
        ref_path = root / "blind/corpus/reference.jsonl"
        refs = [json.loads(line) for line in ref_path.read_text().splitlines()]
        refs[0]["id"] = value
        write_rows(ref_path, refs)
    rows[0][field] = value
    write_rows(path, rows)
    with pytest.raises(ValueError, match="overlap"):
        load_experiment(root, base)


def test_rejects_eval_marked_as_additional_training(experiment: tuple[Path, Path]) -> None:
    root, base = experiment
    path = root / "train-additions/corpus/inputs.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]["split"] = "eval"
    write_rows(path, rows)
    with pytest.raises(ValueError, match="marked train"):
        load_experiment(root, base)


def test_acquisition_prefix_keeps_groups_and_is_nested() -> None:
    rows = [{"id": f"{group}-{i}", "group_id": str(group), "acquisition_order": group}
            for group in (2, 0, 1) for i in range(2)]
    two = select_group_prefix(rows, 2)
    four = select_group_prefix(rows, 4)
    assert [row["id"] for row in two] == ["0-0", "0-1"]
    assert four[:2] == two
    with pytest.raises(ValueError, match="split a group"):
        select_group_prefix(rows, 3)


def test_full_fit_matches_existing_and_ablation_zeros_only_numeric_features() -> None:
    labels = [TeacherLabel(str(i), 0, text, text, score, "codex", is_final=True)
              for i, (text, score) in enumerate([
                  ("明日の予定を教えて", .95), ("でも明日の", .1),
                  ("聞こえますか", .9), ("今日の予定だけど", .2),
              ])]
    config = HashRidgeConfig(hash_size=16, ridge_lambda=.01)
    expected = fit_hash_ridge_model(labels, config)
    full = fit_probe(labels, config, char_only=False)
    ablated = fit_probe(labels, config, char_only=True)
    np.testing.assert_allclose(full.weights, expected.weights)
    assert full.bias == pytest.approx(expected.bias)
    np.testing.assert_allclose(ablated.weights[16:], 0, atol=1e-12)
    for label in labels:
        features = hashed_features(label.prefix_text, config, is_final=True)
        score = np.dot(ablated.weights[:16], features[:16]) + ablated.bias
        assert ablated.predict(label.prefix_text, is_final=True) == pytest.approx(
            max(0.0, min(1.0, score)))


def test_metrics_preserve_pair_order_when_fixed_threshold_misses_ready() -> None:
    rows = [{"id": "p", "group_id": "g", "ready": True, "category": "test"},
            {"id": "n", "group_id": "g", "ready": False, "category": "test"},
            {"id": "a", "group_id": "g2", "ready": None, "category": "ambiguous"}]
    result = metric_bundle(rows, {"p": .6, "n": .2, "a": .9}, threshold=.75)
    assert result["overall"]["false_wait_count"] == 1
    assert result["pair_ranking"]["strict_order_accuracy"] == 1
    assert result["overall"]["ambiguous_excluded"] == 1
    assert choose_dev_threshold(rows, {"p": .6, "n": .2, "a": .9}) == .6


def test_fit_receives_only_teacher_labels_not_reference_or_eval(
    experiment: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    import probe_feature_learning as probe

    root, base = experiment
    real_fit = probe.fit_probe
    observed = []

    def guarded_fit(labels: list[TeacherLabel], config: HashRidgeConfig, *, char_only: bool):
        assert all(not label.utterance_id.startswith(("dev", "blind")) for label in labels)
        for label in labels:
            assert label.saturation == (.95 if int(label.utterance_id.rsplit("-", 1)[1]) % 2 == 0
                                        else .1)
            assert label.is_final is True
        observed.append(len(labels))
        return real_fit(labels, config, char_only=char_only)

    monkeypatch.setattr(probe, "fit_probe", guarded_fit)
    for folder in (base, root / "train-additions"):
        path = folder / "corpus/reference.jsonl"
        refs = [json.loads(line) for line in path.read_text().splitlines()]
        for ref in refs:
            ref["ready"] = not ref["ready"]
        write_rows(path, refs)
    result = run_experiment(root, base, config=HashRidgeConfig(hash_size=16, ridge_lambda=.01),
                            targeted_sizes=(2, 4, 6), latency_repeats=10)
    assert len(observed) == 24
    assert result["protocol"]["reference_used_for_training"] is False
    assert result["protocol"]["dev_is_known_from_previous_experiment"] is True
    assert result["conditions"]["combined12"]["train_count"] == 16
    assert result["conditions"]["baseline_char_only"]["predict_latency_ms"]["count"] == 10
    assert (root / "results/summary.json").is_file()
    assert (root / "results/predictions.jsonl").is_file()
