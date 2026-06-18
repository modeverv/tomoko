from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from server.shared.candidate import (
    ArrivalContextSnapshot,
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
from server.thinker.pregenerator import PregenerationResult


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


class RecordingPregenerator:
    def __init__(self) -> None:
        self.calls: list[datetime | None] = []

    async def pregenerate_once(
        self,
        *,
        now: datetime | None = None,
    ) -> PregenerationResult:
        self.calls.append(now)
        return PregenerationResult(
            scanned_count=2,
            pregenerated_count=1,
            error_count=0,
            elapsed_ms=1.0,
        )


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
    pregenerator = RecordingPregenerator()
    thinker = ThinkerProcess(
        store=store,
        sources=[source],
        evaluator=evaluator,
        pregenerator=pregenerator,  # type: ignore[arg-type]
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
    assert result.pregeneration is not None
    assert result.pregeneration.pregenerated_count == 1
    assert result.arrival is not None
    assert result.arrival.behavior == "wait_silent"
    active = await store.fetch_active_utterance_candidates(now=now, limit=10)
    assert [candidate.maturity for candidate in active] == [1, 0]
    assert active[0].generated_text == "そろそろ休憩する？"
    assert source.calls == 1
    assert pregenerator.calls == [now]
    assert evaluator.calls[0][1].device_id == "desk"


@pytest.mark.unit
async def test_arrival_precompute_deletes_expired_arrivals_older_than_seven_days() -> None:
    now = datetime(2026, 5, 24, 15, 0, tzinfo=UTC)
    store = InMemoryCandidateStore()
    old_expired = await store.insert_arrival_candidate(
        context_snapshot=ArrivalContextSnapshot(computed_at=now),
        behavior="wait_silent",
        computed_at=now - timedelta(days=8),
        valid_until=now - timedelta(days=7, seconds=1),
    )
    recent_expired = await store.insert_arrival_candidate(
        context_snapshot=ArrivalContextSnapshot(computed_at=now),
        behavior="wait_silent",
        computed_at=now - timedelta(hours=2),
        valid_until=now - timedelta(hours=1),
    )
    thinker = ThinkerProcess(
        store=store,
        sources=[],
        evaluator=RecordingEvaluator(),  # type: ignore[arg-type]
        arrival_precomputer=ArrivalPrecomputer(
            store=store,
            router=FakeRouter(),  # type: ignore[arg-type]
        ),
        config=ThinkerLoopConfig(device_id="desk"),
    )

    result = await thinker.run_arrival_precompute_once(now=now)

    assert result.deleted_expired_arrival_count == 1
    assert old_expired.id not in {candidate.id for candidate in store.arrival_candidates}
    assert recent_expired.id in {candidate.id for candidate in store.arrival_candidates}


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
