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
