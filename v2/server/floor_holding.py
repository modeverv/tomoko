from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class HoldingAction(StrEnum):
    WAIT = "wait"
    CONTINUE = "continue"
    YIELD = "yield"
    CAP = "cap"


@dataclass(slots=True)
class HoldingStateMachine:
    max_count: int = 2
    max_total_sec: float = 12.0
    count: int = 0
    total_sec: float = 0.0

    def decide(
        self,
        *,
        pause_ms: int,
        desire: float,
        floor_available: float,
        fatigue: float,
        stop_pressure: float,
        user_speaking: bool,
    ) -> tuple[HoldingAction, float]:
        if user_speaking:
            return HoldingAction.YIELD, 0.0
        if self.count >= self.max_count or self.total_sec >= self.max_total_sec:
            return HoldingAction.CAP, 0.0
        hold_score = desire * 0.45 + floor_available * 0.35 - fatigue * 0.1 - stop_pressure * 0.1
        if pause_ms >= 600 and hold_score >= 0.55:
            self.count += 1
            self.total_sec += pause_ms / 1000.0
            return HoldingAction.CONTINUE, hold_score
        return HoldingAction.WAIT, hold_score
