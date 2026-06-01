from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal, Protocol

from server.shared.models import MemoryHit

MemoryIntent = Literal[
    "recall_request",
    "calendar_request",
    "self_statement",
    "chitchat",
    "unclear",
]


@dataclass(frozen=True)
class MemoryRetrievalPlan:
    intent: MemoryIntent
    retrieve_long_term: bool
    retrieve_calendar: bool
    reason: str


@dataclass(frozen=True)
class MemoryGateRequest:
    text: str
    intent: MemoryIntent
    memories: list[MemoryHit]


@dataclass(frozen=True)
class MemoryGateDecision:
    intent: MemoryIntent
    exposed_memories: list[MemoryHit]
    suppressed_memories: list[MemoryHit]
    reason: str
    source_counts: dict[str, int] = field(default_factory=dict)

    @property
    def retrieved_count(self) -> int:
        return len(self.exposed_memories) + len(self.suppressed_memories)

    @property
    def exposed_count(self) -> int:
        return len(self.exposed_memories)

    @property
    def suppressed_count(self) -> int:
        return len(self.suppressed_memories)


class MemoryGate(Protocol):
    def classify_intent(self, text: str) -> MemoryIntent: ...

    def plan_retrieval(
        self,
        *,
        text: str,
        base_deep_memory: bool,
        calendar_cue: bool,
    ) -> MemoryRetrievalPlan: ...

    def filter_for_prompt(self, request: MemoryGateRequest) -> MemoryGateDecision: ...


class RuleBasedMemoryGate:
    def classify_intent(self, text: str) -> MemoryIntent:
        normalized = text.strip()
        if not normalized:
            return "unclear"
        if _is_self_statement(normalized):
            return "self_statement"
        if _has_calendar_request(normalized):
            return "calendar_request"
        if _has_recall_request(normalized):
            return "recall_request"
        if _is_chitchat(normalized):
            return "chitchat"
        return "unclear"

    def plan_retrieval(
        self,
        *,
        text: str,
        base_deep_memory: bool,
        calendar_cue: bool,
    ) -> MemoryRetrievalPlan:
        intent = self.classify_intent(text)
        if intent == "calendar_request":
            return MemoryRetrievalPlan(
                intent=intent,
                retrieve_long_term=False,
                retrieve_calendar=True,
                reason="calendar_request",
            )
        if intent == "recall_request":
            return MemoryRetrievalPlan(
                intent=intent,
                retrieve_long_term=base_deep_memory,
                retrieve_calendar=calendar_cue,
                reason="recall_request",
            )
        return MemoryRetrievalPlan(
            intent=intent,
            retrieve_long_term=False,
            retrieve_calendar=False,
            reason=f"{intent}_suppresses_retrieval",
        )

    def filter_for_prompt(self, request: MemoryGateRequest) -> MemoryGateDecision:
        if request.intent == "recall_request":
            exposed = list(request.memories)
            suppressed: list[MemoryHit] = []
            reason = "recall_request"
        elif request.intent == "calendar_request":
            exposed = [memory for memory in request.memories if _is_calendar_memory(memory)]
            exposed_ids = {id(memory) for memory in exposed}
            suppressed = [
                memory for memory in request.memories if id(memory) not in exposed_ids
            ]
            reason = "calendar_only"
        else:
            exposed = []
            suppressed = list(request.memories)
            reason = f"{request.intent}_suppressed"

        return MemoryGateDecision(
            intent=request.intent,
            exposed_memories=exposed,
            suppressed_memories=suppressed,
            reason=reason,
            source_counts=_source_counts(request.memories),
        )


class LoggingMemoryGate:
    def __init__(
        self,
        inner: MemoryGate,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._inner = inner
        self._logger = logger or logging.getLogger(__name__)

    def classify_intent(self, text: str) -> MemoryIntent:
        return self._inner.classify_intent(text)

    def plan_retrieval(
        self,
        *,
        text: str,
        base_deep_memory: bool,
        calendar_cue: bool,
    ) -> MemoryRetrievalPlan:
        plan = self._inner.plan_retrieval(
            text=text,
            base_deep_memory=base_deep_memory,
            calendar_cue=calendar_cue,
        )
        self._logger.info(
            "memory_gate plan intent=%s retrieve_long_term=%s retrieve_calendar=%s "
            "base_deep_memory=%s calendar_cue=%s reason=%s text=%r",
            plan.intent,
            plan.retrieve_long_term,
            plan.retrieve_calendar,
            base_deep_memory,
            calendar_cue,
            plan.reason,
            text,
        )
        return plan

    def filter_for_prompt(self, request: MemoryGateRequest) -> MemoryGateDecision:
        decision = self._inner.filter_for_prompt(request)
        top_suppressed = (
            decision.suppressed_memories[0].text[:120]
            if decision.suppressed_memories
            else ""
        )
        self._logger.info(
            "memory_gate filter intent=%s retrieved=%s exposed=%s suppressed=%s "
            "reason=%s sources=%s top_suppressed=%r text=%r",
            decision.intent,
            decision.retrieved_count,
            decision.exposed_count,
            decision.suppressed_count,
            decision.reason,
            decision.source_counts,
            top_suppressed,
            request.text,
        )
        return decision


def _is_self_statement(text: str) -> bool:
    self_statement_markers = (
        "覚えております",
        "覚えています",
        "覚えてます",
        "記憶しています",
        "記憶してます",
    )
    if not any(marker in text for marker in self_statement_markers):
        return False
    return not any(marker in text for marker in ("?", "？", "教えて", "思い出"))


def _has_calendar_request(text: str) -> bool:
    if _looks_like_clock_query(text):
        return False
    return any(
        cue in text
        for cue in (
            "予定",
            "スケジュール",
            "カレンダー",
            "今日",
            "明日",
            "あした",
            "明後日",
            "あさって",
            "今週",
            "来週",
            "空いてる",
            "会議",
            "ミーティング",
            "MTG",
            "mtg",
        )
    )


def _looks_like_clock_query(text: str) -> bool:
    normalized = text.replace(" ", "").replace("　", "")
    return (
        "今何時" in normalized
        or "いま何時" in normalized
        or "何時ぐらい" in normalized
        or "何時くらい" in normalized
        or "何時かわかる" in normalized
        or normalized in {"何時", "時刻", "現在時刻"}
    )


def _has_recall_request(text: str) -> bool:
    if any(cue in text for cue in ("前回", "この前", "こないだ", "以前", "昔")):
        return True
    if "思い出" in text:
        return True
    if "覚えてる" in text or "覚えている" in text:
        return any(cue in text for cue in ("?", "？", "か", "教えて", "数字", "話"))
    if "覚え" in text and "教えて" in text:
        return True
    if "さっき" in text and any(cue in text for cue in ("覚え", "話", "続き")):
        return True
    if "それ" in text and any(cue in text for cue in ("詳しく", "続き")):
        return True
    if "どういう" in text and "っけ" in text:
        return True
    return False


def _is_chitchat(text: str) -> bool:
    stripped = text.strip("。！？!? ")
    return stripped in {
        "こんにちは",
        "おはよう",
        "こんばんは",
        "ありがとう",
        "ありがと",
        "うん",
        "はい",
        "そうだね",
    }


def _is_calendar_memory(memory: MemoryHit) -> bool:
    return bool(memory.source_id and memory.source_id.startswith("calendar:"))


def _source_counts(memories: list[MemoryHit]) -> dict[str, int]:
    counts = {
        "calendar": 0,
        "session_summary": 0,
        "turn_snippet": 0,
        "persona": 0,
        "short_memory": 0,
        "other": 0,
    }
    for memory in memories:
        source_id = memory.source_id or ""
        if source_id.startswith("calendar:"):
            counts["calendar"] += 1
        elif source_id.startswith("session_summary:"):
            counts["session_summary"] += 1
        elif source_id.startswith("restored_turn:") or source_id.startswith("turn:"):
            counts["turn_snippet"] += 1
        elif source_id.startswith("persona:") or source_id.startswith("lexicon:"):
            counts["persona"] += 1
        elif source_id.startswith("short_memory:"):
            counts["short_memory"] += 1
        else:
            counts["other"] += 1
    return counts
