from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest

from server.shared.candidate import (
    CandidateSeed,
    InMemoryCandidateStore,
)
from server.shared.inference.backends.base import InferenceBackend
from server.thinker.arrival import ArrivalPrecomputer, ArrivalStats


class FakeBackend(InferenceBackend):
    def __init__(self, chunks: list[str]) -> None:
        self.name = "fake"
        self.privacy_allowed = True
        self.chunks = chunks
        self.system_prompt = ""
        self.messages: list[dict[str, str]] = []

    async def chat_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
    ) -> AsyncGenerator[str, None]:
        self.system_prompt = system_prompt
        self.messages = messages
        for chunk in self.chunks:
            yield chunk


class RecordingRouter:
    def __init__(self, backend: InferenceBackend | None = None) -> None:
        self.backend = backend or FakeBackend([])
        self.selections: list[tuple[str, str]] = []

    async def select(self, role: str, preference: str = "latency") -> InferenceBackend:
        self.selections.append((role, preference))
        return self.backend


class FailingRouter:
    async def select(self, role: str, preference: str = "latency") -> InferenceBackend:
        del role, preference
        raise RuntimeError("backend unavailable")


class FakeArrivalStatsReader:
    async def read_arrival_stats(
        self,
        *,
        now: datetime,
        device_id: str | None,
    ) -> ArrivalStats:
        assert device_id == "kitchen"
        return ArrivalStats(
            time_since_last_session_sec=420,
            session_count_today=3,
            persona_hint="帰ってきた気配には短く反応する",
        )


@pytest.mark.unit
async def test_arrival_precompute_saves_fresh_candidate() -> None:
    now = datetime(2026, 5, 24, 19, 0, tzinfo=UTC)
    store = InMemoryCandidateStore()
    await store.insert_seed_candidate_once(
        CandidateSeed(
            seed_text="洗濯物をまだ取り込んでいないかもしれない",
            source="unit",
            priority=0.9,
            urgent=True,
            expires_at=now + timedelta(minutes=10),
            dedupe_key="unit:laundry",
        ),
        created_at=now,
    )
    router = RecordingRouter(
        FakeBackend(
            [
                '{"behavior": "speak_first", ',
                '"utterance_text": "おかえり。洗濯物、あとで見ておく？", ',
                '"reason": "urgent seed がある"}',
            ]
        )
    )
    precomputer = ArrivalPrecomputer(
        store=store,
        router=router,  # type: ignore[arg-type]
        stats_reader=FakeArrivalStatsReader(),
    )

    candidate = await precomputer.precompute_once(now=now, device_id="kitchen")

    assert candidate.behavior == "speak_first"
    assert candidate.utterance_text == "おかえり。洗濯物、あとで見ておく？"
    assert candidate.computed_at == now
    assert candidate.valid_until == now + timedelta(minutes=3)
    assert candidate.context_snapshot.device_id == "kitchen"
    assert candidate.context_snapshot.local_time == "19:00"
    assert candidate.context_snapshot.time_since_last_session_sec == 420
    assert candidate.context_snapshot.session_count_today == 3
    assert candidate.context_snapshot.urgent_candidate_count == 1
    assert candidate.context_snapshot.top_urgent_seeds == (
        "洗濯物をまだ取り込んでいないかもしれない",
    )
    assert candidate.context_snapshot.persona_hint == "帰ってきた気配には短く反応する"
    assert router.selections == [("candidate_gen", "privacy")]

    fresh = await store.fetch_latest_fresh_arrival_candidate(
        now=now,
        device_id="kitchen",
    )
    assert fresh == candidate


@pytest.mark.unit
async def test_arrival_precompute_saves_wait_silent_fallback_on_llm_failure() -> None:
    now = datetime(2026, 5, 24, 19, 0, tzinfo=UTC)
    store = InMemoryCandidateStore()
    precomputer = ArrivalPrecomputer(
        store=store,
        router=FailingRouter(),  # type: ignore[arg-type]
    )

    candidate = await precomputer.precompute_once(now=now, device_id=None)

    assert candidate.behavior == "wait_silent"
    assert candidate.utterance_text is None
    assert candidate.valid_until == now + timedelta(minutes=3)
    assert candidate.context_snapshot.device_id is None


@pytest.mark.unit
async def test_arrival_precompute_discards_invalid_speak_first_text() -> None:
    now = datetime(2026, 5, 24, 19, 0, tzinfo=UTC)
    store = InMemoryCandidateStore()
    precomputer = ArrivalPrecomputer(
        store=store,
        router=RecordingRouter(FakeBackend(['{"behavior": "speak_first"}'])),  # type: ignore[arg-type]
    )

    candidate = await precomputer.precompute_once(now=now, device_id="kitchen")

    assert candidate.behavior == "wait_silent"
    assert candidate.utterance_text is None


@pytest.mark.unit
async def test_arrival_precompute_context_snapshot_round_trips() -> None:
    now = datetime(2026, 5, 24, 19, 0, tzinfo=UTC)
    store = InMemoryCandidateStore()
    precomputer = ArrivalPrecomputer(
        store=store,
        router=RecordingRouter(
            FakeBackend(
                [
                    '{"behavior": "subtle_react", "utterance_text": null, ',
                    '"reason": "表示だけ変える余地を残す"}',
                ]
            )
        ),  # type: ignore[arg-type]
    )

    candidate = await precomputer.precompute_once(now=now, device_id="living")
    round_tripped = candidate.context_snapshot.from_json(
        candidate.context_snapshot.to_json()
    )

    assert round_tripped == candidate.context_snapshot


@pytest.mark.unit
async def test_expired_arrival_candidate_is_not_fetched() -> None:
    now = datetime(2026, 5, 24, 19, 0, tzinfo=UTC)
    store = InMemoryCandidateStore()
    precomputer = ArrivalPrecomputer(store=store, router=FailingRouter())  # type: ignore[arg-type]
    candidate = await precomputer.precompute_once(now=now, device_id="kitchen")

    assert (
        await store.fetch_latest_fresh_arrival_candidate(
            now=candidate.valid_until + timedelta(seconds=1),
            device_id="kitchen",
        )
        is None
    )
