from __future__ import annotations

import sys
from pathlib import Path

import pytest

MAKE_MODEL_DIR = Path(__file__).resolve().parents[2] / "make-model"
sys.path.insert(0, str(MAKE_MODEL_DIR))

from benchmark_saturation_latency import measure_predict_latency  # noqa: E402
from combine_teacher_labels import combine_label_rows  # noqa: E402
from generate_synthetic_saturation_corpus import (  # noqa: E402
    SYNTHETIC_SOURCE,
    build_synthetic_utterances,
    write_synthetic_dataset,
)
from generate_teacher_labels import select_prefix_rows  # noqa: E402
from make_anchor_teacher_labels import (  # noqa: E402
    build_anchor_labels,
    build_contrastive_anchor_labels,
    build_life_command_anchor_labels,
    build_referential_anchor_labels,
)
from make_model.corpus import CorpusUtterance, build_prefix_examples, load_corpus  # noqa: E402
from make_model.japanese_daily_dialogue import (  # noqa: E402
    convert_japanese_daily_dialogue,
    load_japanese_daily_dialogue,
)
from make_model.model import (  # noqa: E402
    EXTRA_FEATURES,
    HashRidgeConfig,
    HashRidgeSaturationModel,
    evaluate_model,
    hashed_features,
)
from make_model.schema import PrefixExample, TeacherLabel, read_jsonl, write_jsonl  # noqa: E402
from make_model.teacher import (  # noqa: E402
    OpenAICompatibleTeacher,
    TeacherConfig,
    label_prefix_examples,
)
from make_model.training import TrainConfig, train_hash_ridge_model  # noqa: E402
from split_teacher_labels import split_rows  # noqa: E402

from server.tomoko.semantic import SATURATION_SYSTEM_PROMPT, saturation_prompt  # noqa: E402

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


def test_build_synthetic_utterances_are_repo_owned_and_deterministic() -> None:
    utterances = build_synthetic_utterances(utterance_count=20, seed=20260620)

    assert utterances == build_synthetic_utterances(utterance_count=20, seed=20260620)
    assert len(utterances) == 20
    assert all(utterance.source == SYNTHETIC_SOURCE for utterance in utterances)
    assert all(utterance.utterance_id.startswith("synthetic-codex-") for utterance in utterances)
    assert all("japanese-daily-dialogue" not in utterance.source for utterance in utterances)


def test_write_synthetic_dataset_writes_corpus_prefixes_and_manifest(tmp_path: Path) -> None:
    summary = write_synthetic_dataset(
        out_dir=tmp_path,
        utterance_count=12,
        seed=20260620,
        min_chars=1,
        stride_chars=2,
        max_prefixes_per_utterance=5,
    )

    assert summary.utterance_count == 12
    assert summary.prefix_count > 12
    assert read_jsonl(tmp_path / "corpus.jsonl")[0]["source"] == SYNTHETIC_SOURCE
    assert read_jsonl(tmp_path / "prefixes.jsonl")[0]["source"] == SYNTHETIC_SOURCE
    manifest = (tmp_path / "manifest.json").read_text(encoding="utf-8")
    assert "No internet dialogue corpus is used" in manifest


def test_combine_label_rows_deduplicates_by_label_identity(tmp_path: Path) -> None:
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    row = {
        "utterance_id": "u1",
        "prefix_index": 0,
        "prefix_text": "今日の予定を教えて",
        "full_text": "今日の予定を教えて",
        "saturation": 0.9,
        "teacher_model": "teacher",
        "label_source": "teacher_llm",
    }
    write_jsonl(first, [row])
    write_jsonl(second, [row, {**row, "label_source": "manual_anchor"}])

    combined = combine_label_rows([first, second])

    assert len(combined) == 2
    assert {item["label_source"] for item in combined} == {"teacher_llm", "manual_anchor"}


def test_measure_predict_latency_reports_hot_loop_stats() -> None:
    def predict(text: str, *, is_final: bool = False) -> float:
        assert text == "今日の予定を教えて"
        assert is_final is False
        return 0.5

    result = measure_predict_latency(
        predict,
        text="今日の予定を教えて",
        repeats=5,
        warmup=2,
        is_final=False,
    )

    assert result["repeats"] == 5
    assert result["warmup"] == 2
    assert result["last_prediction"] == 0.5
    assert result["mean_ms"] >= 0.0
    assert result["p50_ms"] >= 0.0
    assert result["p95_ms"] >= result["p50_ms"]


def test_split_rows_creates_seeded_disjoint_train_eval_sets() -> None:
    rows = [{"utterance_id": f"u{index}", "prefix_index": index} for index in range(10)]

    train_rows, eval_rows = split_rows(rows, train_size=8, seed=42)

    assert split_rows(rows, train_size=8, seed=42) == (train_rows, eval_rows)
    assert len(train_rows) == 8
    assert len(eval_rows) == 2
    train_ids = {(row["utterance_id"], row["prefix_index"]) for row in train_rows}
    eval_ids = {(row["utterance_id"], row["prefix_index"]) for row in eval_rows}
    assert train_ids.isdisjoint(eval_ids)
    assert train_ids | eval_ids == {
        (row["utterance_id"], row["prefix_index"]) for row in rows
    }


def test_build_anchor_labels_contains_manual_high_and_low_examples() -> None:
    anchors = build_anchor_labels(count=1000)

    assert len(anchors) == 1000
    assert len({anchor.utterance_id for anchor in anchors}) == 1000
    assert all(anchor.label_source == "manual_anchor" for anchor in anchors)

    by_text = {anchor.prefix_text: anchor for anchor in anchors}
    assert by_text["今日の予定を教えて"].saturation == pytest.approx(0.95)
    assert by_text["聞こえますか"].saturation == pytest.approx(0.9)
    assert by_text["えっと"].saturation == pytest.approx(0.1)
    assert by_text["ただ、やっぱり"].saturation == pytest.approx(0.2)


def test_build_contrastive_anchor_labels_marks_trailing_reversal_as_low() -> None:
    anchors = build_contrastive_anchor_labels(count=1000)

    assert len(anchors) == 1000
    assert len({anchor.utterance_id for anchor in anchors}) == 1000
    assert all(anchor.label_source == "manual_contrastive_anchor" for anchor in anchors)

    by_text = {anchor.prefix_text: anchor for anchor in anchors}
    assert by_text["それが良いと思うがしかし"].saturation == pytest.approx(0.22)
    assert by_text["それが良いと思うがしかし"].is_final is True
    assert by_text["それが良いと思うけど"].saturation < 0.35
    assert by_text["今日は進めたい。だけど"].saturation < 0.35


def test_build_referential_anchor_labels_marks_pronoun_completion_as_high() -> None:
    anchors = build_referential_anchor_labels(count=1000)

    assert len(anchors) == 1000
    assert len({anchor.utterance_id for anchor in anchors}) == 1000
    assert all(anchor.label_source == "manual_referential_anchor" for anchor in anchors)

    by_text = {anchor.prefix_text: anchor for anchor in anchors}
    assert by_text["それが良いと思う"].saturation == pytest.approx(0.82)
    assert by_text["それで問題ない"].saturation == pytest.approx(0.85)
    assert by_text["その方向でいい"].saturation == pytest.approx(0.84)
    assert by_text["これは違うと思う"].saturation == pytest.approx(0.78)


def test_build_life_command_anchor_labels_marks_tomoko_commands_as_high() -> None:
    anchors = build_life_command_anchor_labels(count=1000)

    assert len(anchors) == 1000
    assert len({anchor.utterance_id for anchor in anchors}) == 1000
    assert all(anchor.label_source == "manual_life_command_anchor" for anchor in anchors)

    by_text = {anchor.prefix_text: anchor for anchor in anchors}
    assert by_text["トモコ、今忙しい？"].saturation == pytest.approx(0.86)
    assert by_text["トモコ、テレビ付けて"].saturation == pytest.approx(0.9)
    assert by_text["トモコ、ネットスーパーのラフを作って"].saturation == pytest.approx(0.9)


def test_hashed_features_marks_contrastive_tail_without_penalizing_prefix() -> None:
    config = HashRidgeConfig(hash_size=16, ngram_min=1, ngram_max=2)
    contrastive_index = config.hash_size + EXTRA_FEATURES.index("contrastive_tail")

    assert hashed_features("それが良いと思うがしかし", config)[contrastive_index] == 1.0
    assert hashed_features("それが良いと思うけど", config)[contrastive_index] == 1.0
    assert hashed_features("今日は進めたい。だけど", config)[contrastive_index] == 1.0
    assert hashed_features("それが良いと思う", config)[contrastive_index] == 0.0


def test_hash_ridge_model_can_predict_old_artifact_without_new_extra_feature() -> None:
    config = HashRidgeConfig(hash_size=16, ngram_min=1, ngram_max=2)
    old_feature_count = config.hash_size + len(EXTRA_FEATURES) - 1
    model = HashRidgeSaturationModel(
        config=config,
        weights=[0.0] * old_feature_count,
        bias=0.5,
        metadata={},
    )

    assert model.predict("それが良いと思うがしかし", is_final=True) == pytest.approx(0.5)


def test_make_model_teacher_uses_runtime_e2b_saturation_prompt_contract() -> None:
    teacher = OpenAICompatibleTeacher(
        url="http://127.0.0.1:8082",
        model="mlx-community/gemma-4-26b-a4b-it-4bit",
    )

    expected_user_prompt = saturation_prompt("こんにちは聞こえますか")
    payload = teacher.payload(expected_user_prompt)
    user_prompt = payload["messages"][1]["content"]

    assert payload["messages"] == [
        {"role": "system", "content": SATURATION_SYSTEM_PROMPT},
        {"role": "user", "content": expected_user_prompt},
    ]
    assert "意味飽和度を採点" not in payload["messages"][0]["content"]
    assert "会話相手が今返し始めてよい度合い" in user_prompt
    for required_fragment in [
        "入力された日本語の途中/最終発話について、",
        "高い値:",
        "- 質問、依頼、確認、呼びかけ、返答待ちが明確",
        "- 文が完結している、または途中でも相手が短く返せる",
        "- 「聞こえますか」「いますか」「どうですか」「お願い」「教えて」など",
        "低い値:",
        "- 「えっと」「あの」「ただ」「でも」「というか」など、まだ続きそう",
        "- 文の途中で、相手が返すと割り込みになりそう",
        "- 意味が取れない短すぎる断片",
        "TEXT=えっと\nSATURATION=0.10",
        "TEXT=今日の予定を教えて\nSATURATION=0.95",
        "TEXT=ただ、やっぱり\nSATURATION=0.20",
        "TEXT=今の返事ちゃんと聞こえてる\nSATURATION=0.85",
        "TEXT=あのさ、昨日の\nSATURATION=0.25",
        "TEXT=こんにちは聞こえますか",
    ]:
        assert required_fragment in user_prompt


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
