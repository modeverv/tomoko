from __future__ import annotations

import pytest

from server.gateway.reply_audio import ReplyAudioPipeline
from server.shared.models import ThinkingEvent


@pytest.mark.unit
def test_reply_audio_pipeline_emits_emotion_before_tts_text() -> None:
    pipeline = ReplyAudioPipeline()

    emotion_commands = pipeline.handle_event(ThinkingEvent(type="emotion", value="happy"))
    text_commands = pipeline.handle_event(
        ThinkingEvent(type="text_delta", value="うん、聞こえるよ。")
    )

    assert emotion_commands[0].action == "emotion"
    assert emotion_commands[0].value == "happy"
    assert emotion_commands[0].image == "/assets/images/tomoko-happy.svg"
    assert [command.action for command in text_commands] == ["text_delta", "tts_text"]
    assert text_commands[1].value == "うん、聞こえるよ。"
    assert text_commands[1].style == "happy"


@pytest.mark.unit
def test_reply_audio_pipeline_flushes_sentence_then_final_remainder() -> None:
    pipeline = ReplyAudioPipeline()

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
