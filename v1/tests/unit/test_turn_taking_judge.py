from __future__ import annotations

import pytest

from server.gateway.turn_taking.judge import RuleFirstTurnTakingJudge
from server.shared.models import TurnTakingAudioMetrics, TurnTakingInput


def _input(
    text: str,
    *,
    segment_ms: float = 500,
    rms_db: float = -24,
    active_frame_ratio: float = 0.8,
    pending_reply_state: str = "generating_not_started",
) -> TurnTakingInput:
    return TurnTakingInput(
        pending_reply_state=pending_reply_state,  # type: ignore[arg-type]
        new_transcript=text,
        audio_metrics=TurnTakingAudioMetrics(
            segment_ms=segment_ms,
            rms_db=rms_db,
            peak_db=-12,
            active_frame_ratio=active_frame_ratio,
        ),
        attention_mode="engaged",
        playback_state="idle",
    )


@pytest.mark.unit
async def test_rule_judge_keeps_current_reply_for_empty_transcript() -> None:
    decision = await RuleFirstTurnTakingJudge().judge(_input(""))

    assert decision.decision == "continue_current_reply"
    assert decision.reason == "empty_transcript"


@pytest.mark.unit
async def test_rule_judge_ignores_short_low_signal_segment() -> None:
    decision = await RuleFirstTurnTakingJudge().judge(
        _input("ん", segment_ms=180, rms_db=-46, active_frame_ratio=0.1)
    )

    assert decision.decision == "ignore_as_noise"
    assert decision.reason == "short_low_signal"


@pytest.mark.unit
async def test_rule_judge_stops_on_clear_stop_word() -> None:
    decision = await RuleFirstTurnTakingJudge().judge(_input("ストップして"))

    assert decision.decision == "stop_speaking"
    assert decision.reason == "stop_keyword"


@pytest.mark.unit
async def test_rule_judge_stops_on_wait_word() -> None:
    decision = await RuleFirstTurnTakingJudge().judge(
        _input("ちょっと待って", pending_reply_state="audio_started")
    )

    assert decision.decision == "stop_speaking"
    assert decision.reason == "wait_keyword"


@pytest.mark.unit
async def test_rule_judge_stops_on_wait_inflection() -> None:
    decision = await RuleFirstTurnTakingJudge().judge(
        _input("この映像ちょっとちょっと待とうか", pending_reply_state="audio_started")
    )

    assert decision.decision == "stop_speaking"
    assert decision.reason == "wait_keyword"


@pytest.mark.unit
async def test_rule_judge_restarts_on_correction() -> None:
    decision = await RuleFirstTurnTakingJudge().judge(_input("いや違う、そうじゃなくて"))

    assert decision.decision == "restart_with_new_input"
    assert decision.reason == "restart_keyword"


@pytest.mark.unit
async def test_rule_judge_restarts_on_long_followup() -> None:
    decision = await RuleFirstTurnTakingJudge().judge(
        _input(
            "さっきの話にもう少し足したいことがある",
            pending_reply_state="text_started",
        )
    )

    assert decision.decision == "restart_with_new_input"
    assert decision.reason == "substantial_new_input"
