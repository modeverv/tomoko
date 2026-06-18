from __future__ import annotations

import pytest

from _tools.bench_tts_ready_prompt import evaluate_tts_ready, split_emotion_header


@pytest.mark.unit
def test_split_emotion_header_returns_body_and_emotion() -> None:
    body, emotion, has_header = split_emotion_header(
        "EMOTION:happy\nうん、午後三時からだよ。"
    )

    assert body == "うん、午後三時からだよ。"
    assert emotion == "happy"
    assert has_header is True


@pytest.mark.unit
def test_evaluate_tts_ready_rejects_english_mix() -> None:
    ready, needs_normalize, has_terminal_punctuation = evaluate_tts_ready(
        "EMOTION:neutral\nmeeting は 3pm からだよ。"
    )

    assert ready is False
    assert needs_normalize is True
    assert has_terminal_punctuation is True


@pytest.mark.unit
def test_evaluate_tts_ready_accepts_readable_japanese() -> None:
    ready, needs_normalize, has_terminal_punctuation = evaluate_tts_ready(
        "EMOTION:neutral\n会議は午後三時からだよ。"
    )

    assert ready is True
    assert needs_normalize is False
    assert has_terminal_punctuation is True
