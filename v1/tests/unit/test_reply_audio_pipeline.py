from __future__ import annotations

import pytest

from server.gateway.reply import ReplyPipeline
from server.shared.models import ThinkingEvent


@pytest.mark.unit
def test_reply_audio_pipeline_emits_emotion_before_tts_text() -> None:
    pipeline = ReplyPipeline()

    emotion_commands = pipeline.handle_event(ThinkingEvent(type="emotion", value="happy"))
    text_commands = pipeline.handle_event(
        ThinkingEvent(type="text_delta", value="うん、聞こえるよ。")
    )

    assert emotion_commands[0].action == "emotion"
    assert emotion_commands[0].value == "happy"
    assert emotion_commands[0].image == "/assets/images/tomoko-happy.svg"
    assert emotion_commands[0].style == "happy"
    assert [command.action for command in text_commands] == ["text_delta", "tts_text"]
    assert text_commands[1].value == "うん、聞こえるよ。"
    assert text_commands[1].style == "happy"


@pytest.mark.unit
def test_reply_audio_pipeline_flushes_sentence_then_final_remainder() -> None:
    pipeline = ReplyPipeline()

    first = pipeline.handle_event(ThinkingEvent(type="text_delta", value="うん"))
    second = pipeline.handle_event(ThinkingEvent(type="text_delta", value="。聞こえる"))
    third = pipeline.handle_event(ThinkingEvent(type="text_delta", value="よ"))
    done = pipeline.handle_event(ThinkingEvent(type="done", value=""))

    assert [command.action for command in first] == ["text_delta"]
    assert [(command.action, command.value) for command in second] == [
        ("text_delta", "。聞こえる"),
        ("tts_text", "うん。"),
    ]
    assert [command.action for command in third] == ["text_delta"]
    assert [(command.action, command.value) for command in done] == [
        ("tts_text", "聞こえるよ"),
        ("done", ""),
    ]
    assert pipeline.reply_text == "うん。聞こえるよ"


@pytest.mark.unit
def test_reply_audio_pipeline_does_not_flush_japanese_comma_boundaries() -> None:
    pipeline = ReplyPipeline()

    first = pipeline.handle_event(ThinkingEvent(type="text_delta", value="トモコ、"))
    second = pipeline.handle_event(
        ThinkingEvent(type="text_delta", value="today の meeting は 3pm からだから、")
    )
    third = pipeline.handle_event(
        ThinkingEvent(type="text_delta", value="schedule を確認して。")
    )

    assert [command.action for command in first] == ["text_delta"]
    assert [command.action for command in second] == ["text_delta"]
    assert ("tts_text", "トモコ、today の meeting は 3pm からだから、schedule を確認して。") in [
        (command.action, command.value) for command in third
    ]


@pytest.mark.unit
def test_reply_audio_pipeline_removes_english_delta_before_display_but_keeps_tts_text() -> None:
    pipeline = ReplyPipeline()

    first = pipeline.handle_event(
        ThinkingEvent(type="text_delta", value=" hear you, 何か")
    )
    second = pipeline.handle_event(
        ThinkingEvent(type="text_delta", value="ありますか？")
    )

    assert [(command.action, command.value) for command in first] == [
        ("text_delta", "何か")
    ]
    assert [(command.action, command.value) for command in second] == [
        ("text_delta", "ありますか？"),
        ("tts_text", "hear you, 何かありますか？"),
    ]
    assert pipeline.reply_text == "何かありますか？"


@pytest.mark.unit
def test_reply_audio_pipeline_removes_split_english_terms() -> None:
    pipeline = ReplyPipeline()

    commands = []
    for delta in ["「", "ブル", "ーム", "の", " TAX", "ON", "OM", "Y", "」", "です。"]:
        commands.extend(pipeline.handle_event(ThinkingEvent(type="text_delta", value=delta)))

    assert pipeline.reply_text == "「ブルームの」です。"
    display_values = [
        command.value for command in commands if command.action == "text_delta"
    ]
    assert all("TAXONOMY" not in value for value in display_values)
    assert ("tts_text", "「ブルームの TAXONOMY」です。") in [
        (command.action, command.value) for command in commands
    ]
