from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
SOURCE = Path(__file__).resolve().parents[2] / "make-model/collect_teacher_comparison.py"
SPEC = importlib.util.spec_from_file_location("collect_teacher_comparison", SOURCE)
assert SPEC and SPEC.loader
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


def test_parse_labels_requires_exact_unique_ids_and_numeric_scores() -> None:
    result = collector.parse_labels(
        '{"labels":[{"id":"b","score":1},{"id":"a","score":0}]}', ["a", "b"]
    )
    assert result == {"a": 0.0, "b": 1.0}


@pytest.mark.parametrize("score", [-0.1, 1.1, True, "0.8", None, float("nan"), float("inf")])
def test_parse_labels_rejects_invalid_scores(score: object) -> None:
    with pytest.raises(ValueError):
        collector.parse_labels(json.dumps({"labels": [{"id": "a", "score": score}]}), ["a"])


@pytest.mark.parametrize("ids", [[], ["a", "a"], ["a", "b"], ["b"]])
def test_parse_labels_rejects_missing_extra_or_duplicate_ids(ids: list[str]) -> None:
    with pytest.raises(ValueError):
        collector.parse_labels(
            json.dumps({"labels": [{"id": key, "score": 0.5} for key in ids]}), ["a"]
        )


def test_prompt_exposes_only_id_and_utterance() -> None:
    prompt = collector.batch_prompt([{
        "id": "r1", "text": "今は何時", "group_id": "HIDDEN_GROUP",
        "split": "HIDDEN_SPLIT", "category": "HIDDEN_CATEGORY", "score": "HIDDEN_SCORE",
    }])
    assert "今は何時" in prompt
    assert "r1" in prompt
    assert "今日の予定を教えて" in prompt
    assert "会話相手が今返し始めてよい度合い" in prompt
    assert "HIDDEN" not in prompt
    assert '"group_id"' not in prompt
    assert '"split"' not in prompt


def test_batch_order_is_deterministic_without_mixing_splits() -> None:
    rows = [
        {"id": str(i), "text": str(i), "split": "train" if i < 12 else "eval"}
        for i in range(20)
    ]
    first = collector.ordered_batches(rows, 5)
    assert first == collector.ordered_batches(list(reversed(rows)), 5)
    assert all(len({row["split"] for row in batch}) == 1 for batch in first)
    assert {row["id"] for batch in first for row in batch} == {row["id"] for row in rows}
    assert all(len(batch) <= 5 for batch in first)


def test_codex_trace_rejects_tool_use_and_uses_final_message() -> None:
    events = [
        {"type": "item.completed", "item": {"type": "agent_message", "text": "prelude"}},
        {"type": "item.completed", "item": {"type": "reasoning", "text": "reasoning"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": '{"labels":[]}'}},
        {"type": "turn.completed", "usage": {"input_tokens": 123}},
    ]
    result, usage = collector.parse_codex_trace("\n".join(map(json.dumps, events)))
    assert result == '{"labels":[]}'
    assert usage == {"input_tokens": 123}
    events.insert(0, {"type": "item.started", "item": {"type": "command_execution"}})
    with pytest.raises(ValueError, match="tool"):
        collector.parse_codex_trace("\n".join(map(json.dumps, events)))


def test_codex_trace_rejects_incomplete_or_failed_turns() -> None:
    with pytest.raises(ValueError):
        collector.parse_codex_trace('{"type":"turn.failed","error":{"message":"failure"}}')


def test_batch_size_must_be_positive() -> None:
    with pytest.raises(ValueError):
        collector.ordered_batches([], 0)


def test_collection_retains_failure_without_publishing_partial_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "corpus").mkdir()
    (tmp_path / "corpus/inputs.jsonl").write_text(
        '{"id":"a","text":"人工例","split":"train"}\n', encoding="utf-8"
    )
    # MOCK: unit-only transport response tests atomic publication on invalid labels.
    monkeypatch.setattr(collector, "run_batch", lambda *_args: '{"labels":[]}')
    with pytest.raises(ValueError, match="missing or extra"):
        collector.collect(tmp_path, "gemma", 20)
    assert not (tmp_path / "labels/gemma.jsonl").exists()
    raw = json.loads((tmp_path / "batches/gemma/000.json").read_text())
    assert "missing or extra" in raw["error"]
    assert raw["elapsed_ms"] >= 0


def test_codex_command_uses_isolated_stdin_and_current_defaults(tmp_path: Path) -> None:
    command = collector.codex_command(tmp_path, tmp_path / "schema.json")
    assert command[-1] == "-"
    assert "--ignore-user-config" in command
    assert "--ephemeral" in command
    assert command[command.index("-C") + 1] == str(tmp_path)
    assert command[command.index("-m") + 1] == "gpt-6-astra"
    assert 'model_reasoning_effort="medium"' in command


def test_short_ids_map_reordered_output_by_explicit_lookup() -> None:
    rows = [{"id": "original-a", "text": "人工一"}, {"id": "original-b", "text": "人工二"}]
    inputs, mapping = collector.local_id_inputs(rows)
    assert inputs == [{"id": "1", "text": "人工一"}, {"id": "2", "text": "人工二"}]
    assert mapping == {"1": "original-a", "2": "original-b"}
    assert "original-a" not in collector.batch_prompt(inputs)
    result = collector.mapped_labels(
        '{"labels":[{"id":"2","score":0.2},{"id":"1","score":0.9}]}', mapping
    )
    assert result == {"original-a": 0.9, "original-b": 0.2}


def test_short_id_mapping_rejects_missing_id_instead_of_assigning_by_order() -> None:
    with pytest.raises(ValueError, match="missing or extra"):
        collector.mapped_labels(
            '{"labels":[{"id":"2","score":0.9}]}', {"1": "original-a", "2": "original-b"}
        )
