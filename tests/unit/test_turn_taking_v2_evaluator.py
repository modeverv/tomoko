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

    # STT partial の末尾欠け（「〜けど」→「〜け」）を文末と誤検出しないこと
    res_truncated = SemanticFinishJudge.evaluate("細かいことなんですけ")
    assert res_truncated["semantic_saturation"] < 0.50

    # 丁寧形の過去は文末として検出されること
    res_polite_past = SemanticFinishJudge.evaluate("出発は朝の六時に決めました。")
    assert res_polite_past["semantic_saturation"] >= 0.75

    # 促音便の過去形（〜った）も文末として検出されること
    res_plain_past = SemanticFinishJudge.evaluate("あ、わかった。")
    assert res_plain_past["semantic_saturation"] >= 0.75


@pytest.mark.unit
def test_speech_motivation_evaluator() -> None:
    res_talking = SpeechMotivationEvaluator.evaluate(
        semantic_saturation=0.90,
        remaining_info_risk=0.10,
        semantic_split_risk=0.0,
        confidence=0.8,
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
        confidence=0.95,
        vad_state="idle",
        attention_mode="engaged",
        audio_level_db=-50.0,
    )
    assert res_finished["speech_decision_score"] >= 0.75
    assert res_finished["proposal"] in ("full_response_candidate", "floor_grab_candidate")


@pytest.mark.unit
def test_fusion_fires_on_quiet_stable_complete() -> None:
    # 静か + テキスト確定 + 高い意味完了 → fusion 発火
    res = SpeechMotivationEvaluator.evaluate(
        semantic_saturation=0.85,
        remaining_info_risk=0.15,
        semantic_split_risk=0.0,
        confidence=0.95,
        vad_state="listening",
        attention_mode="engaged",
        audio_level_db=-60.0,  # quiet（発話は終わっているが VAD 未発火）
        tail_stable=True,
    )
    assert res["would_start_inference_fusion"] is True
    # 既存ロジックは listening 中は発火しない（挙動不変）
    assert res["would_start_inference"] is False


@pytest.mark.unit
def test_fusion_blocked_while_speaking_without_stability() -> None:
    # 発話中（音声大）+ 確定なし → 高 saturation でも fusion は発火しない
    res = SpeechMotivationEvaluator.evaluate(
        semantic_saturation=0.95,
        remaining_info_risk=0.05,
        semantic_split_risk=0.0,
        confidence=1.0,
        vad_state="listening",
        attention_mode="engaged",
        audio_level_db=-25.0,  # speaking
        tail_stable=False,
    )
    assert res["would_start_inference_fusion"] is False


@pytest.mark.unit
def test_fusion_p_yielding_lifts_borderline_case() -> None:
    # 意味完了がやや弱い境界ケース（quiet+stable でもしきい値未満）を
    # VAP 高確度が押し上げる: 0.5*0.51 + 0.1 + 0.2 = 0.555 < 0.6 <= +0.095
    kwargs = dict(
        semantic_saturation=0.60,
        remaining_info_risk=0.40,
        semantic_split_risk=0.0,
        confidence=0.85,
        vad_state="listening",
        attention_mode="engaged",
        audio_level_db=-60.0,
        tail_stable=True,
    )
    without_vap = SpeechMotivationEvaluator.evaluate(**kwargs)
    with_vap = SpeechMotivationEvaluator.evaluate(**kwargs, p_yielding=0.95)
    assert without_vap["would_start_inference_fusion"] is False
    assert with_vap["would_start_inference_fusion"] is True


@pytest.mark.unit
def test_fusion_args_do_not_change_legacy_judgment() -> None:
    # p_yielding / tail_stable を渡しても既存の would_start_inference は変わらない
    base = dict(
        semantic_saturation=0.9,
        remaining_info_risk=0.1,
        semantic_split_risk=0.0,
        confidence=0.9,
        vad_state="idle",
        attention_mode="engaged",
        audio_level_db=None,
    )
    legacy = SpeechMotivationEvaluator.evaluate(**base)
    with_fusion_args = SpeechMotivationEvaluator.evaluate(
        **base, p_yielding=0.99, tail_stable=True
    )
    assert legacy["would_start_inference"] == with_fusion_args["would_start_inference"]
    assert legacy["speech_decision_score"] == with_fusion_args["speech_decision_score"]
    assert legacy["proposal"] == with_fusion_args["proposal"]
