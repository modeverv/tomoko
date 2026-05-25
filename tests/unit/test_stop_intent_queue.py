from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from server.gateway.stop_intent import (
    InMemoryStopIntentStore,
    LLMStopIntentClassifier,
    StopIntentClassifierWorker,
    StopIntentObservation,
    build_stop_observation,
    should_adopt_stop_signal,
    should_record_stop_intent_candidate,
)
from server.shared.inference.backends.base import InferenceBackend
from server.shared.models import SessionEvent


class SlowJSONBackend(InferenceBackend):
    name = "slow_json"
    privacy_allowed = True

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def chat_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
    ) -> AsyncGenerator[str, None]:
        del system_prompt, messages
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        yield '{"predicted_kind":"soft_stop","confidence":0.93,"reason":"wait"}'


class FakeRouter:
    def __init__(self, backend: SlowJSONBackend) -> None:
        self.backend = backend

    async def select(self, role: str, preference: str):
        del role, preference
        return self.backend


def _observation(text: str = "その話はいったん置いといて") -> StopIntentObservation:
    return build_stop_observation(
        transcript_text=text,
        conversation_session_id=None,
        turn_id="turn-1",
        rule_kind="stop_candidate",
        adopted_action="observer",
        playback_state_json={"playback_state": "speaking"},
        reply_state_json={
            "first_reply_text_emitted": False,
            "first_audio_chunk_emitted": False,
        },
    )


@pytest.mark.unit
def test_stop_intent_candidate_lexical_detection() -> None:
    assert should_record_stop_intent_candidate("その話いったん置いといて")
    assert should_adopt_stop_signal("soft_stop", 0.9)
    assert not should_adopt_stop_signal("defer", 0.99)


@pytest.mark.unit
async def test_in_memory_store_claims_only_one_pending_observation() -> None:
    store = InMemoryStopIntentStore()
    first = _observation("今は聞けない")
    second = _observation("あとにして")
    await store.insert_observation(first)
    await store.insert_observation(second)

    claimed = await asyncio.gather(
        store.claim_next_observation(),
        store.claim_next_observation(),
    )

    assert sorted(item.id for item in claimed if item is not None) == sorted(
        [first.id, second.id]
    )
    assert all(
        store.observations[item.id].status == "processing"
        for item in claimed
        if item is not None
    )


@pytest.mark.unit
async def test_worker_records_signals_and_emits_advisory_event() -> None:
    store = InMemoryStopIntentStore()
    observation = _observation()
    await store.insert_observation(observation)
    events: list[SessionEvent] = []

    async def callback(event: SessionEvent) -> None:
        events.append(event)

    worker = StopIntentClassifierWorker(store=store, result_callback=callback)

    assert await worker.process_once() is True

    assert store.observations[observation.id].status == "completed"
    assert {signal.method for signal in store.signals} == {"rule", "embedding"}
    assert any(
        event.type == "stop_intent_classified"
        and event.payload["observation_id"] == str(observation.id)
        for event in events
    )


@pytest.mark.unit
async def test_llm_classifier_is_limited_to_one_concurrent_call() -> None:
    store = InMemoryStopIntentStore()
    now = datetime.now(UTC)
    for index in range(3):
        await store.insert_observation(
            StopIntentObservation(
                id=uuid4(),
                transcript_id=f"t-{index}",
                transcript_text="今は聞けない",
                rule_kind="stop_candidate",
                adopted_action="observer",
                turn_id=f"turn-{index}",
                created_at=now,
            )
        )
    backend = SlowJSONBackend()
    worker = StopIntentClassifierWorker(
        store=store,
        llm_classifier=LLMStopIntentClassifier(FakeRouter(backend)),  # type: ignore[arg-type]
    )

    await asyncio.gather(worker.process_once(), worker.process_once(), worker.process_once())

    assert backend.max_active == 1
    assert len([signal for signal in store.signals if signal.method == "llm"]) == 3
