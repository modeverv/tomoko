from __future__ import annotations

from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any

import pytest

from server.gateway.reply.speech_normalizer import (
    ReplySpeechNormalizer,
    _polish_common_tts_terms,
    _restore_terminal_punctuation,
)
from server.session import TomoroSession
from server.shared.models import AudioChunkOut, TTSInput


class FakeTokenizer:
    def apply_chat_template(
        self,
        messages: list[dict[str, str]],
        *,
        tokenize: bool,
        add_generation_prompt: bool,
    ) -> str:
        assert tokenize is False
        assert add_generation_prompt is True
        return messages[-1]["content"]


@pytest.mark.unit
async def test_speech_normalizer_skips_plain_japanese_without_loading_model() -> None:
    loaded: list[str] = []
    normalizer = ReplySpeechNormalizer(
        model_loader=lambda model_name: loaded.append(model_name),
    )

    result = await normalizer.normalize("うん、今日はゆっくり話すね。")

    assert result == "うん、今日はゆっくり話すね。"
    assert loaded == []


@pytest.mark.unit
async def test_speech_normalizer_uses_gemma_for_english_and_time_mix() -> None:
    loaded: list[str] = []
    prompts: list[str] = []

    def load_model(model_name: str) -> tuple[object, FakeTokenizer]:
        loaded.append(model_name)
        return object(), FakeTokenizer()

    def stream_generate(
        model: Any,
        tokenizer: Any,
        prompt: str,
        max_tokens: int,
    ):
        prompts.append(prompt)
        yield SimpleNamespace(
            text="トモコ、今日の会議は午後三時からだよ。",
            generation_tokens=16,
            generation_tps=80.0,
        )

    normalizer = ReplySpeechNormalizer(
        model_name="fake-gemma",
        model_loader=load_model,
        stream_generator=stream_generate,
    )

    result = await normalizer.normalize("トモコ、today の meeting は 3pm からだよ。")

    assert result == "トモコ、今日の会議は午後三時からだよ。"
    assert loaded == ["fake-gemma"]
    assert prompts == ["トモコ、today の meeting は 3pm からだよ。"]


@pytest.mark.unit
def test_speech_normalizer_restores_terminal_punctuation() -> None:
    assert (
        _restore_terminal_punctuation(
            "トモコ今日の会議は午後三時からだからスケジュールを確認して",
            source="トモコ、today の meeting は 3pm からだから、schedule を確認して。",
        )
        == "トモコ今日の会議は午後三時からだからスケジュールを確認して。"
    )
    assert (
        _restore_terminal_punctuation(
            "終わったらすぐに教えて",
            source="終わったら quick に教えて。",
        )
        == "終わったらすぐに教えて。"
    )


@pytest.mark.unit
def test_speech_normalizer_polishes_common_tts_terms() -> None:
    assert _polish_common_tts_terms("終わったらクイックに教えて") == "終わったらすぐに教えて"


class FakeSpeechNormalizer:
    async def normalize(self, text: str) -> str:
        assert text == "today は 3pm。"
        return "今日は午後三時。"


class FakeTTSBackend:
    name = "fake_tts"

    def __init__(self) -> None:
        self.inputs: list[TTSInput] = []

    async def synthesize(
        self,
        tts_input: TTSInput,
    ) -> AsyncGenerator[AudioChunkOut, None]:
        self.inputs.append(tts_input)
        yield AudioChunkOut(data=b"audio", sequence=0, is_last=True)


@pytest.mark.unit
async def test_session_normalizes_text_before_tts_synthesis() -> None:
    sent_audio: list[bytes] = []
    tts = FakeTTSBackend()
    session = TomoroSession(
        vad_processor=object(),  # type: ignore[arg-type]
        send_event=lambda event: None,
        send_audio=sent_audio.append,
        tts_backend=tts,  # type: ignore[arg-type]
        speech_normalizer=FakeSpeechNormalizer(),  # type: ignore[arg-type]
    )

    await session._flush_tts_text("today は 3pm。", style="neutral")

    assert tts.inputs == [TTSInput(text="今日は午後三時。", style="neutral")]
    assert sent_audio == [b"audio"]
