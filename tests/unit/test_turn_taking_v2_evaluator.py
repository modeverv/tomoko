from __future__ import annotations

import pytest

from server.gateway.turn_taking.v2_evaluator import (
    TranscriptValidity,
    StablePrefixExtractor,
    SemanticFinishJudge,
    SpeechMotivationEvaluator,
)


@pytest.mark.unit
def test_transcript_validity() -> None:
    assert TranscriptValidity.evaluate("昨日の件なんだけど") is True
    assert TranscriptValidity.evaluate("進めていいと思う？") is True

    assert TranscriptValidity.evaluate("") is False
    assert TranscriptValidity.evaluate("っ") is False
    assert TranscriptValidity.evaluate("。") is False

    assert TranscriptValidity.evaluate("視聴ありがとうございました") is False
    assert TranscriptValidity.evaluate("チャンネル登録よろしくね") is False

    assert TranscriptValidity.evaluate("ああああああ") is False
    assert TranscriptValidity.evaluate("ですですですですですです") is False


@pytest.mark.unit
def test_stable_prefix_extractor() -> None:
    assert StablePrefixExtractor.extract([], "昨日の件") == ""

    history = ["昨日の件", "昨日の件なんだけど"]
    assert StablePrefixExtractor.extract(history, "昨日の件なんだけど、あれ") == "昨日の件なんだけど"

    history = ["昨日の件なんだけど"]
    assert StablePrefixExtractor.extract(history, "昨日の犬なんだけど") == "昨日の"


@pytest.mark.unit
def test_semantic_finish_judge() -> None:
    res_unfinished = SemanticFinishJudge.evaluate("昨日の件なんだけど")
    assert res_unfinished["semantic_saturation"] < 0.50
    assert res_unfinished["safe_response_level"] <= 2

    res_finished = SemanticFinishJudge.evaluate("進めていいと思う。")
    assert res_finished["semantic_saturation"] >= 0.80
    assert res_finished["safe_response_level"] >= 4

    res_question = SemanticFinishJudge.evaluate("進めていいと思う？")
    assert res_question["semantic_saturation"] >= 0.90
    assert res_question["safe_response_level"] == 5

    res_split = SemanticFinishJudge.evaluate("進めていいと思う？ただ")
    assert res_split["semantic_split_risk"] >= 0.80
    assert res_split["semantic_saturation"] <= 0.40


@pytest.mark.unit
def test_speech_motivation_evaluator() -> None:
    res_talking = SpeechMotivationEvaluator.evaluate(
        semantic_saturation=0.90,
        remaining_info_risk=0.10,
        semantic_split_risk=0.0,
        vad_state="listening",
        attention_mode="engaged",
        audio_level_db=-15.0,
    )
    assert res_talking["speech_decision_score"] < 0.50
    assert res_talking["proposal"] in ("silence", "prepare_only")

    res_finished = SpeechMotivationEvaluator.evaluate(
        semantic_saturation=0.95,
        remaining_info_risk=0.05,
        semantic_split_risk=0.0,
        vad_state="idle",
        attention_mode="engaged",
        audio_level_db=-50.0,
    )
    assert res_finished["speech_decision_score"] >= 0.75
    assert res_finished["proposal"] in ("full_response_candidate", "floor_grab_candidate")
