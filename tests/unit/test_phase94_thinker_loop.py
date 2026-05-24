from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from server.shared.candidate import (
    CandidateSeed,
    EvaluatedUtterance,
    InMemoryCandidateStore,
    ThinkerEvaluationContext,
    ThinkerSourceContext,
)
from server.thinker.arrival import ArrivalPrecomputer
from server.thinker.main import (
    ThinkerLoopConfig,
    ThinkerProcess,
    candidate_generation_loop,
)


@dataclass
class FakeSource:
    seed: CandidateSeed
    calls: int = 0

    async def collect(self, context: ThinkerSourceContext) -> list[CandidateSeed]:
        assert context.device_id == "desk"
        self.calls += 1
        return [self.seed]


class FailingSource:
    async def collect(self, context: ThinkerSourceContext) -> list[CandidateSeed]:
        del context
        raise RuntimeError("source failed")


class RecordingEvaluator:
    def __init__(self) -> None:
        self.calls: list[tuple[CandidateSeed, ThinkerEvaluationContext]] = []

    async def evaluate(
        self,
        seed: CandidateSeed,
        context: ThinkerEvaluationContext,
    ) -> EvaluatedUtterance | None:
        self.calls.append((seed, context))
        return EvaluatedUtterance(
            should_keep=True,
            generated_text="そろそろ休憩する？",
            priority=0.8,
            urgent=True,
            reason="unit",
            context_tags=(*seed.context_tags, "evaluated_by:unit"),
        )


class FailingEvaluator:
    async def evaluate(
        self,
        seed: CandidateSeed,
        context: ThinkerEvaluationContext,
    ) -> EvaluatedUtterance | None:
        del seed, context
        raise RuntimeError("eval failed")


class FakeBackend:
    name = "fake"
    privacy_allowed = True

    async def chat_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
    ):
        del system_prompt, messages
        yield '{"behavior": "wait_silent", "utterance_text": null, "reason": "unit"}'


class FakeRouter:
    async def select(self, role: str, preference: str = "latency") -> FakeBackend:
        assert role == "candidate_gen"
        assert preference == "privacy"
        return FakeBackend()


def _seed(now: datetime) -> CandidateSeed:
    return CandidateSeed(
        seed_text="午後なので軽く様子を聞く",
        source="unit",
        priority=0.4,
        urgent=False,
        expires_at=now + timedelta(minutes=30),
        dedupe_key="unit:afternoon",
    )


@pytest.mark.unit
async def test_run_once_generates_candidate_and_arrival() -> None:
    now = datetime(2026, 5, 24, 15, 0, tzinfo=UTC)
    store = InMemoryCandidateStore()
    source = FakeSource(_seed(now))
    evaluator = RecordingEvaluator()
    thinker = ThinkerProcess(
        store=store,
        sources=[source],
        evaluator=evaluator,
        arrival_precomputer=ArrivalPrecomputer(
            store=store,
            router=FakeRouter(),  # type: ignore[arg-type]
        ),
        config=ThinkerLoopConfig(device_id="desk"),
    )

    result = await thinker.run_once(now=now)

    assert result.candidate.generated_seed_count == 1
    assert result.candidate.inserted_seed_count == 1
    assert result.candidate.kept_candidate_count == 1
    assert result.arrival is not None
    assert result.arrival.behavior == "wait_silent"
    active = await store.fetch_active_utterance_candidates(now=now, limit=10)
    assert [candidate.maturity for candidate in active] == [1, 0]
    assert active[0].generated_text == "そろそろ休憩する？"
    assert source.calls == 1
    assert evaluator.calls[0][1].device_id == "desk"


@pytest.mark.unit
async def test_candidate_generation_survives_source_and_evaluator_failure() -> None:
    now = datetime(2026, 5, 24, 15, 0, tzinfo=UTC)
    store = InMemoryCandidateStore()
    thinker = ThinkerProcess(
        store=store,
        sources=[FailingSource(), FakeSource(_seed(now))],
        evaluator=FailingEvaluator(),  # type: ignore[arg-type]
        config=ThinkerLoopConfig(device_id="desk"),
    )

    result = await thinker.run_candidate_generation_once(now=now)

    assert result.generated_seed_count == 1
    assert result.inserted_seed_count == 1
    assert result.kept_candidate_count == 0
    assert result.error_count == 2
    active = await store.fetch_active_utterance_candidates(now=now, limit=10)
    assert len(active) == 1
    assert active[0].maturity == 0


@pytest.mark.unit
async def test_candidate_generation_loop_stops_on_cancellation() -> None:
    now = datetime(2026, 5, 24, 15, 0, tzinfo=UTC)
    store = InMemoryCandidateStore()
    thinker = ThinkerProcess(
        store=store,
        sources=[FakeSource(_seed(now))],
        evaluator=RecordingEvaluator(),  # type: ignore[arg-type]
        config=ThinkerLoopConfig(device_id="desk", candidate_interval_sec=30.0),
    )
    sleep_started = asyncio.Event()

    async def cancellable_sleep(delay: float) -> None:
        assert delay == 30.0
        sleep_started.set()
        await asyncio.sleep(60)

    task = asyncio.create_task(
        candidate_generation_loop(
            thinker,
            sleep=cancellable_sleep,
            now_factory=lambda: now,
        )
    )
    await asyncio.wait_for(sleep_started.wait(), timeout=1.0)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
