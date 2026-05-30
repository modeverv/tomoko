from __future__ import annotations

from datetime import UTC, datetime

import pytest

from server.session_memory_gate import (
    LoggingMemoryGate,
    MemoryGateRequest,
    RuleBasedMemoryGate,
)
from server.shared.models import MemoryHit


def _memory(text: str, *, source_id: str | None = None) -> MemoryHit:
    return MemoryHit(
        speaker="tomoko",
        text=text,
        timestamp=datetime(2026, 5, 30, 10, 0, tzinfo=UTC),
        similarity=0.9,
        source_id=source_id,
    )


@pytest.mark.unit
def test_rule_memory_gate_classifies_self_statement_separately() -> None:
    gate = RuleBasedMemoryGate()

    assert gate.classify_intent("普通に覚えております") == "self_statement"
    assert gate.classify_intent("さっき覚えてる数字を教えてください") == (
        "recall_request"
    )


@pytest.mark.unit
def test_rule_memory_gate_suppresses_self_statement_memory() -> None:
    gate = RuleBasedMemoryGate()
    memory = _memory(
        "会話セッション要約: 数字の「123」を覚えておくよう指示があった。",
        source_id="session_summary:123",
    )

    decision = gate.filter_for_prompt(
        MemoryGateRequest(
            text="普通に覚えております",
            intent="self_statement",
            memories=[memory],
        )
    )

    assert decision.exposed_memories == []
    assert decision.suppressed_memories == [memory]
    assert decision.reason == "self_statement_suppressed"
    assert decision.source_counts["session_summary"] == 1
    assert decision.source_counts["persona"] == 0
    assert decision.source_counts["short_memory"] == 0


@pytest.mark.unit
def test_rule_memory_gate_uses_calendar_only_for_calendar_request() -> None:
    gate = RuleBasedMemoryGate()
    calendar = _memory("カレンダー予定: 13:00 家族の予定", source_id="calendar:gcal")
    summary = _memory("会話セッション要約: 123 の話", source_id="session_summary:123")

    decision = gate.filter_for_prompt(
        MemoryGateRequest(
            text="今日の予定ある？",
            intent="calendar_request",
            memories=[calendar, summary],
        )
    )

    assert decision.exposed_memories == [calendar]
    assert decision.suppressed_memories == [summary]
    assert decision.reason == "calendar_only"


@pytest.mark.unit
def test_rule_memory_gate_plans_retrieval_separately_from_use() -> None:
    gate = RuleBasedMemoryGate()

    recall_plan = gate.plan_retrieval(
        text="この前の話覚えてる？",
        base_deep_memory=True,
        calendar_cue=False,
    )
    self_statement_plan = gate.plan_retrieval(
        text="普通に覚えております",
        base_deep_memory=True,
        calendar_cue=False,
    )

    assert recall_plan.retrieve_long_term is True
    assert recall_plan.retrieve_calendar is False
    assert self_statement_plan.retrieve_long_term is False
    assert self_statement_plan.reason == "self_statement_suppresses_retrieval"


class RecordingLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, message: str, *args: object) -> None:
        self.messages.append(message % args)


@pytest.mark.unit
def test_logging_memory_gate_logs_plan_and_filter() -> None:
    logger = RecordingLogger()
    gate = LoggingMemoryGate(RuleBasedMemoryGate(), logger=logger)  # type: ignore[arg-type]

    plan = gate.plan_retrieval(
        text="普通に覚えております",
        base_deep_memory=True,
        calendar_cue=False,
    )
    gate.filter_for_prompt(
        MemoryGateRequest(
            text="普通に覚えております",
            intent=plan.intent,
            memories=[_memory("会話セッション要約: 123", source_id="session_summary:123")],
        )
    )

    log_text = "\n".join(logger.messages)
    assert "memory_gate plan intent=self_statement" in log_text
    assert "memory_gate filter intent=self_statement retrieved=1 exposed=0" in log_text
    assert "top_suppressed='会話セッション要約: 123'" in log_text
