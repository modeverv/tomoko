from __future__ import annotations

from datetime import UTC, datetime

import pytest

from server.edge.pipeline.stt_filter import TranscriptFilter
from server.shared.models import Transcript


def _transcript(text: str, *, audio_level_db: float = -20.0, is_final: bool = True) -> Transcript:
    return Transcript(
        text=text,
        device_id="test",
        speaker=None,
        audio_level_db=audio_level_db,
        recorded_at=datetime.now(UTC),
        is_final=is_final,
    )


@pytest.mark.unit
def test_filter_drops_mata_repetition_loop() -> None:
    decision = TranscriptFilter().evaluate(
        _transcript("今日は また また また また また また また")
    )

    assert decision.action == "drop"
    assert decision.reason == "repetition_loop"


@pytest.mark.unit
def test_filter_drops_have_mixed_language_loop() -> None:
    decision = TranscriptFilter().evaluate(
        _transcript("今日は1日 Have a Have Have Have Have")
    )

    assert decision.action == "drop"
    assert decision.reason == "mixed_language_loop"


@pytest.mark.unit
def test_filter_drops_short_phrase_repetition_loop() -> None:
    decision = TranscriptFilter().evaluate(_transcript("今日は日曜日の日曜日です"))

    assert decision.action == "drop"
    assert decision.reason == "repetition_loop"


@pytest.mark.unit
def test_filter_drops_low_audio_otsukaresama() -> None:
    decision = TranscriptFilter().evaluate(
        _transcript("お疲れ様でした", audio_level_db=-25.0)
    )

    assert decision.action == "drop"
    assert decision.reason == "known_hallucination_phrase"


@pytest.mark.unit
def test_filter_drops_low_audio_ascii_only_word() -> None:
    decision = TranscriptFilter().evaluate(
        _transcript("washed", audio_level_db=-29.0)
    )

    assert decision.action == "drop"
    assert decision.reason == "low_audio_ascii_text"


@pytest.mark.unit
def test_filter_accepts_normal_speech() -> None:
    decision = TranscriptFilter().evaluate(
        _transcript("MLXにすると速くなっている気がする")
    )

    assert decision.action == "accept"
    assert decision.reason == "accepted"


@pytest.mark.unit
def test_filter_does_not_drop_short_wake_word() -> None:
    decision = TranscriptFilter().evaluate(_transcript("トモコ"))

    assert decision.action == "accept"
    assert decision.reason == "accepted"


@pytest.mark.unit
def test_filter_suppresses_partial_instead_of_dropping() -> None:
    decision = TranscriptFilter().evaluate(
        _transcript("ご視聴ありがとうございました", is_final=False),
        is_partial=True,
    )

    assert decision.action == "suppress_partial"
    assert decision.reason == "known_hallucination_phrase"
