from __future__ import annotations

from pathlib import Path

import pytest

from server.shared.logging import JsonlLogger
from server.shared.models import (
    SpeechOrder,
    SpeechOrderMode,
    SpeechPressureState,
    SpeechSchedulerInput,
    SpeechSchedulerOutput,
    SpeechSchedulerThresholds,
)
from server.tomoko.db_bridge import (
    insert_scheduler_decision_sql,
    insert_speech_order_sql,
    notify_speech_order_sql,
)
from server.tomoko.scheduler import SpeechScheduler, detect_stop_intent
from server.tomoko.semantic import (
    SemanticSaturationJudge,
    deterministic_saturation,
    parse_saturation_output,
    stable_prefix,
)

pytestmark = pytest.mark.unit


def test_parse_saturation_output_accepts_only_single_fixed_line() -> None:
    assert parse_saturation_output("SATURATION=0.72").saturation == 0.72
    assert parse_saturation_output("  SATURATION=1.0  ").saturation == 1.0

    for output in [
        "",
        "SATURATION=1.2",
        "SATURATION=-0.1",
        "REASON=done\nSATURATION=0.8",
        "SATURATION=high",
    ]:
        with pytest.raises(ValueError):
            parse_saturation_output(output)


def test_deterministic_saturation_fallback_handles_representative_cases() -> None:
    assert deterministic_saturation("").saturation == 0.0
    assert deterministic_saturation("え").saturation < 0.25
    assert deterministic_saturation("トモコ、予定を教えて").saturation >= 0.75
    assert deterministic_saturation("これでいい?").saturation >= 0.75
    assert deterministic_saturation("ただ、やっぱり").saturation < 0.45
    assert stable_prefix(["トモコ、今日の予定", "トモコ、今日の予定を"]) == "トモコ、今日の予定"


@pytest.mark.asyncio
async def test_semantic_saturation_judge_falls_back_and_logs(tmp_path: Path) -> None:
    class BrokenBackend:
        async def complete(self, prompt: str) -> str:
            assert "トモコ" in prompt
            return "not fixed line"

    log_path = tmp_path / "semantic.jsonl"
    judge = SemanticSaturationJudge(llm_backend=BrokenBackend(), logger=JsonlLogger(log_path))
    result = await judge.judge("トモコ、予定を教えて")

    assert result.saturation >= 0.75
    assert result.source == "deterministic_fallback"
    assert "semantic_saturation" in log_path.read_text(encoding="utf-8")


def test_speech_scheduler_user_reply_replace_current_and_breakdown() -> None:
    output = SpeechScheduler().decide(
        SpeechSchedulerInput(
            final_stt_text="トモコ、短く返事して",
            semantic_saturation=0.9,
            silence_ms=600,
            p_yielding=0.95,
        )
    )

    assert output.action == "replace_current"
    assert output.text_intent == "reply"
    assert output.score_breakdown["saturation"] > 0
    assert output.llm_prompt_basis


def test_speech_scheduler_appends_calendar_while_speaking() -> None:
    current = SpeechOrder(
        text="先に返事しているよ",
        mode=SpeechOrderMode.REPLACE_CURRENT,
        reason="current",
        priority=50,
    )
    output = SpeechScheduler().decide(
        SpeechSchedulerInput(
            tomoko_currently_speaking=True,
            current_speech_order=current,
            current_speech_score=0.8,
            calendar_urgency=1.0,
            semantic_saturation=0.0,
            silence_ms=0,
        )
    )

    assert output.action == "append_after_current"
    assert output.text_intent == "calendar_notice"
    assert "calendar" in output.reason


def test_speech_scheduler_stop_and_interruption_suppression() -> None:
    scheduler = SpeechScheduler()
    assert detect_stop_intent("トモコ、止めて") == 1.0
    stop = scheduler.decide(SpeechSchedulerInput(final_stt_text="止めて", stop_intent=1.0))
    assert stop.action == "stop"
    assert stop.text_intent == "stop"

    suppress = scheduler.decide(
        SpeechSchedulerInput(
            user_speaking=True,
            tomoko_currently_speaking=True,
            semantic_saturation=0.2,
            pressure_state=SpeechPressureState(interruption_penalty=1.0),
        )
    )
    assert suppress.action == "suppress"
    assert suppress.score < 0


def test_speech_scheduler_replaces_when_new_score_beats_current_margin() -> None:
    current = SpeechOrder(
        text="古い返答",
        mode=SpeechOrderMode.REPLACE_CURRENT,
        reason="old",
        priority=40,
    )
    output = SpeechScheduler(
        thresholds=SpeechSchedulerThresholds(replace_margin=0.2)
    ).decide(
        SpeechSchedulerInput(
            current_speech_order=current,
            current_speech_score=0.4,
            semantic_saturation=1.0,
            p_yielding=1.0,
            final_stt_text="いや、別の質問",
        )
    )

    assert output.action == "replace_current"
    assert output.score > 0.6


def test_scheduler_output_logs_structured_decision(tmp_path: Path) -> None:
    log_path = tmp_path / "runtime.jsonl"
    scheduler = SpeechScheduler(logger=JsonlLogger(log_path))
    output: SpeechSchedulerOutput = scheduler.decide(
        SpeechSchedulerInput(final_stt_text="これでいい?", semantic_saturation=0.8)
    )

    assert output.score_breakdown
    payload = log_path.read_text(encoding="utf-8")
    assert "speech_scheduler_decision" in payload
    assert "score_breakdown" in payload


def test_speech_order_db_bridge_uses_row_body_and_id_only_notify() -> None:
    output = SpeechScheduler().decide(
        SpeechSchedulerInput(final_stt_text="これでいい?", semantic_saturation=0.8)
    )
    order = SpeechOrder(
        text="いいと思うよ。",
        mode=SpeechOrderMode.REPLACE_CURRENT,
        reason=output.reason,
        priority=80,
        scheduler_decision_id=output.id,
    )

    decision_sql = insert_scheduler_decision_sql(
        output,
        stt_observation_id=None,
        semantic_saturation_id=None,
    )
    order_sql = insert_speech_order_sql(order)
    notify_query, notify_params = notify_speech_order_sql(order.id)

    assert "v2_speech_scheduler_decisions" in decision_sql.query
    assert "v2_speech_orders" in order_sql.query
    assert "SELECT pg_notify" in notify_query
    assert notify_params["payload"] == str(order.id)
