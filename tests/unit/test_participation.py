from __future__ import annotations

import pytest

from server.edge.participation.base import ParticipationContext
from server.edge.participation.wake_word import WakeWordJudge


@pytest.mark.unit
async def test_wake_word_triggers() -> None:
    judge = WakeWordJudge()

    result = await judge.judge(ParticipationContext(transcript="トモコ、今日の天気は？"))

    assert result.should_participate is True
    assert result.mode == "called"


@pytest.mark.unit
async def test_no_wake_word_stays_observer() -> None:
    judge = WakeWordJudge()

    result = await judge.judge(ParticipationContext(transcript="今日いい天気だね"))

    assert result.should_participate is False
    assert result.mode == "observer"


@pytest.mark.unit
async def test_wake_word_accepts_latin_name() -> None:
    judge = WakeWordJudge()

    result = await judge.judge(ParticipationContext(transcript="Tomoko, are you there?"))

    assert result.should_participate is True
    assert result.mode == "called"


@pytest.mark.unit
async def test_wake_word_misrecognitions() -> None:
    judge = WakeWordJudge()

    for phrase in [
        "ともく、聞こえますか",
        "トモク、起きて",
        "智子さん",
        "朋子、元気？",
    ]:
        result = await judge.judge(ParticipationContext(transcript=phrase))
        assert result.should_participate is True
        assert result.mode == "called"


@pytest.mark.unit
async def test_engaged_followup_filters_short_noise() -> None:
    judge = WakeWordJudge()

    result = await judge.judge(
        ParticipationContext(
            transcript="ん",
            attention_mode="engaged",
            audio_level_db=-36.0,
        )
    )

    assert result.should_participate is False
    assert result.mode == "observer"
    assert result.reason == "low_confidence_followup"


@pytest.mark.unit
async def test_engaged_followup_filters_whisper_hallucination() -> None:
    judge = WakeWordJudge()

    result = await judge.judge(
        ParticipationContext(
            transcript="字幕をご視聴頂きましてありがとうございました",
            attention_mode="engaged",
            audio_level_db=-24.0,
        )
    )

    assert result.should_participate is False
    assert result.mode == "observer"
    assert result.reason == "low_confidence_followup"


@pytest.mark.unit
async def test_engaged_followup_filters_quiet_short_phrase() -> None:
    judge = WakeWordJudge()

    result = await judge.judge(
        ParticipationContext(
            transcript="お疲れ様です",
            attention_mode="engaged",
            audio_level_db=-35.0,
        )
    )

    assert result.should_participate is False
    assert result.mode == "observer"
    assert result.reason == "low_confidence_followup"


@pytest.mark.unit
async def test_engaged_followup_filters_short_unfinished_fragment() -> None:
    judge = WakeWordJudge()

    for fragment in ("相槌の", "相槌のタイミングで"):
        result = await judge.judge(
            ParticipationContext(
                transcript=fragment,
                attention_mode="engaged",
                audio_level_db=-20.0,
            )
        )

        assert result.should_participate is False
        assert result.mode == "observer"
        assert result.reason == "low_confidence_followup"


@pytest.mark.unit
async def test_engaged_followup_keeps_normal_phrase() -> None:
    judge = WakeWordJudge()

    result = await judge.judge(
        ParticipationContext(
            transcript="どうなんやろうね、あなたができること何か教えて",
            attention_mode="engaged",
            audio_level_db=-15.0,
        )
    )

    assert result.should_participate is True
    assert result.mode == "invited"
