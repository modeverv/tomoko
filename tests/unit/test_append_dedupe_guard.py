from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from server.tomoko.append_dedupe import HashRidgeAppendDedupeGuard

pytestmark = pytest.mark.unit


@dataclass(frozen=True, slots=True)
class _FakeAppendDedupeResult:
    duplicate_score: float
    continuation_score: float
    new_intent_score: float
    label: str


class _FakeAppendDedupeModel:
    def __init__(self, result: _FakeAppendDedupeResult) -> None:
        self.result = result
        self.samples: list[Any] = []

    def predict(self, sample: Any) -> _FakeAppendDedupeResult:
        self.samples.append(sample)
        return self.result


def test_append_dedupe_guard_suppresses_duplicate_only_when_tomoko_output_active() -> None:
    model = _FakeAppendDedupeModel(
        _FakeAppendDedupeResult(
            duplicate_score=0.99,
            continuation_score=0.05,
            new_intent_score=0.04,
            label="duplicate",
        )
    )
    guard = HashRidgeAppendDedupeGuard(model=model)

    idle = guard.inspect(
        previous_user_text="うんあんまりよくわかってない",
        current_user_text="あんまりよくわかってない",
        time_delta_ms=900,
        tomoko_speaking=False,
        speech_queue_active=False,
        current_is_final=True,
    )
    speaking = guard.inspect(
        previous_user_text="うんあんまりよくわかってない",
        current_user_text="あんまりよくわかってない",
        time_delta_ms=900,
        tomoko_speaking=True,
        speech_queue_active=False,
        current_is_final=True,
    )
    queued = guard.inspect(
        previous_user_text="うんあんまりよくわかってない",
        current_user_text="あんまりよくわかってない",
        time_delta_ms=900,
        tomoko_speaking=False,
        speech_queue_active=True,
        current_is_final=True,
    )

    assert idle.should_suppress is False
    assert speaking.should_suppress is True
    assert queued.should_suppress is True
