from __future__ import annotations

import sys
from pathlib import Path

import pytest

MAKE_MODEL_DIR = Path(__file__).resolve().parents[2] / "make-model"
sys.path.insert(0, str(MAKE_MODEL_DIR))

from generate_teacher_labels import select_prefix_rows  # noqa: E402
from make_model.corpus import CorpusUtterance, build_prefix_examples, load_corpus  # noqa: E402
from make_model.japanese_daily_dialogue import (  # noqa: E402
    convert_japanese_daily_dialogue,
    load_japanese_daily_dialogue,
)
from make_model.model import HashRidgeSaturationModel, evaluate_model  # noqa: E402
from make_model.schema import PrefixExample, TeacherLabel, read_jsonl, write_jsonl  # noqa: E402
from make_model.teacher import TeacherConfig, label_prefix_examples  # noqa: E402
from make_model.training import TrainConfig, train_hash_ridge_model  # noqa: E402

pytestmark = pytest.mark.unit


def test_load_corpus_accepts_text_and_jsonl(tmp_path: Path) -> None:
    text_path = tmp_path / "corpus.txt"
    text_path.write_text("今日は予定を教えて\n\nただ、やっぱり明日で\n", encoding="utf-8")

    text_items = load_corpus(text_path)

    assert [item.text for item in text_items] == ["今日は予定を教えて", "ただ、やっぱり明日で"]
    assert text_items[0].source == "corpus.txt:1"

    jsonl_path = tmp_path / "corpus.jsonl"
    write_jsonl(
        jsonl_path,
        [
            {"text": "聞こえますか", "conversation_id": "c1", "turn_index": 2},
            {"utterance": "えっと、昨日の", "source": "manual"},
        ],
    )

    jsonl_items = load_corpus(jsonl_path)

    assert jsonl_items[0].text == "聞こえますか"
    assert jsonl_items[0].conversation_id == "c1"
    assert jsonl_items[0].turn_index == 2
    assert jsonl_items[1].text == "えっと、昨日の"
    assert jsonl_items[1].source == "manual"


def test_load_japanese_daily_dialogue_nested_json(tmp_path: Path) -> None:
    data_dir = tmp_path / "jdd" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "topic1.json").write_text(
        """
        [
          {
            "topic_id": 1,
            "topic_name": "Dailylife",
            "dialogue_id": 101,
            "utterances": [
              {"turn_num": 1, "speaker": "A", "utterance": "おはよう。"},
              {"turn_num": 2, "speaker": "B", "utterance": "今日は予定を教えて。"}
            ]
          }
        ]
        """,
        encoding="utf-8",
    )

    items = load_japanese_daily_dialogue(tmp_path / "jdd")

    assert [item.text for item in items] == ["おはよう。", "今日は予定を教えて。"]
    assert items[0].source == "japanese-daily-dialogue:topic1.json:dialogue=101:turn=1"
    assert items[0].conversation_id == "jdd-topic1-101"
    assert items[1].turn_index == 2


def test_convert_japanese_daily_dialogue_writes_corpus_prefixes_and_manifest(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "jdd" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "topic1.json").write_text(
        """
        {
          "topic_id": 1,
          "topic_name": "Dailylife",
          "dialogues": [
            {
              "dialogue_id": 101,
              "utterances": [
                {"turn_num": 1, "speaker": "A", "utterance": "今日は予定を教えて"}
              ]
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    summary = convert_japanese_daily_dialogue(
        tmp_path / "jdd",
        corpus_out=tmp_path / "corpus.jsonl",
        prefixes_out=tmp_path / "prefixes.jsonl",
        manifest_out=tmp_path / "manifest.json",
        min_chars=2,
        stride_chars=3,
    )

    assert summary.utterance_count == 1
    assert summary.prefix_count == 4
    assert read_jsonl(tmp_path / "corpus.jsonl")[0]["text"] == "今日は予定を教えて"
    assert read_jsonl(tmp_path / "prefixes.jsonl")[0]["prefix_text"] == "今日"
    assert "CC BY-NC-ND 4.0" in (tmp_path / "manifest.json").read_text(encoding="utf-8")


def test_build_prefix_examples_can_emit_one_character_steps() -> None:
    utterance = CorpusUtterance(text="今日の予定を教えて", source="sample", conversation_id="c1")

    examples = build_prefix_examples(
        [utterance],
        min_chars=2,
        stride_chars=1,
        include_final=True,
        max_prefixes_per_utterance=4,
    )

    assert [example.prefix_text for example in examples] == [
        "今日",
        "今日の",
        "今日の予",
        "今日の予定",
    ]
    assert all(example.full_text == utterance.text for example in examples)
    assert all(example.conversation_id == "c1" for example in examples)
    assert examples[0].is_final is False


def test_select_prefix_rows_can_sample_seeded_random_rows() -> None:
    rows = [{"prefix_index": index, "prefix_text": str(index)} for index in range(20)]

    sampled = select_prefix_rows(rows, sample_size=5, sample_seed=7)

    assert sampled == select_prefix_rows(rows, sample_size=5, sample_seed=7)
    assert [row["prefix_index"] for row in sampled] != [0, 1, 2, 3, 4]
    assert [row["prefix_index"] for row in sampled] == sorted(
        row["prefix_index"] for row in sampled
    )
    assert len(sampled) == 5


def test_select_prefix_rows_keeps_limit_and_rejects_mixed_selection_modes() -> None:
    rows = [{"prefix_index": index, "prefix_text": str(index)} for index in range(5)]

    assert select_prefix_rows(rows, limit=3) == rows[:3]
    with pytest.raises(ValueError, match="cannot be used together"):
        select_prefix_rows(rows, limit=3, sample_size=3)


@pytest.mark.asyncio
async def test_label_prefix_examples_uses_teacher_and_fixed_saturation_line() -> None:
    class FakeTeacher:
        async def complete(self, prompt: str) -> str:
            assert "TEXT=今日の予定を教えて" in prompt
            return "SATURATION=0.91"

    labels = await label_prefix_examples(
        [
            PrefixExample(
                utterance_id="u1",
                prefix_index=1,
                prefix_text="今日の予定を教えて",
                full_text="今日の予定を教えて",
                source="unit",
            )
        ],
        teacher=FakeTeacher(),
        config=TeacherConfig(source_model="gemma-4-26b-mlx-4bit"),
    )

    assert len(labels) == 1
    assert labels[0].saturation == 0.91
    assert labels[0].teacher_model == "gemma-4-26b-mlx-4bit"
    assert labels[0].label_source == "teacher_llm"


def test_train_evaluate_and_reload_hash_ridge_model(tmp_path: Path) -> None:
    labels = [
        TeacherLabel(
            utterance_id="u1",
            prefix_index=1,
            prefix_text="えっと",
            full_text="えっと、昨日の予定なんだけど",
            saturation=0.1,
            teacher_model="fake",
        ),
        TeacherLabel(
            utterance_id="u2",
            prefix_index=1,
            prefix_text="今日の予定を教えて",
            full_text="今日の予定を教えて",
            saturation=0.95,
            teacher_model="fake",
            is_final=True,
        ),
        TeacherLabel(
            utterance_id="u3",
            prefix_index=1,
            prefix_text="ただ、やっぱり",
            full_text="ただ、やっぱり明日で",
            saturation=0.2,
            teacher_model="fake",
        ),
        TeacherLabel(
            utterance_id="u4",
            prefix_index=1,
            prefix_text="聞こえますか",
            full_text="聞こえますか",
            saturation=0.85,
            teacher_model="fake",
            is_final=True,
        ),
    ]

    artifact_path = tmp_path / "saturation-model.json"
    model, metrics = train_hash_ridge_model(
        labels,
        TrainConfig(hash_size=64, ngram_min=1, ngram_max=3, ridge_lambda=0.1),
        artifact_path=artifact_path,
    )

    assert artifact_path.exists()
    assert metrics["train_count"] == 4
    assert 0.0 <= model.predict("今日の予定を教えて") <= 1.0

    reloaded = HashRidgeSaturationModel.load(artifact_path)
    assert reloaded.predict("聞こえますか") == pytest.approx(model.predict("聞こえますか"))

    eval_metrics = evaluate_model(reloaded, labels)
    assert eval_metrics["count"] == 4
    assert eval_metrics["mae"] >= 0.0


def test_jsonl_helpers_round_trip_dataclasses(tmp_path: Path) -> None:
    path = tmp_path / "labels.jsonl"
    label = TeacherLabel(
        utterance_id="u1",
        prefix_index=0,
        prefix_text="こんにちは",
        full_text="こんにちは",
        saturation=0.8,
        teacher_model="fake",
    )

    write_jsonl(path, [label.to_json()])

    assert read_jsonl(path) == [label.to_json()]
