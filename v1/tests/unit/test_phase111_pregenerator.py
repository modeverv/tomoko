from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from server.shared.candidate import InMemoryCandidateStore
from server.shared.inference.tts.base import TTSBackend
from server.shared.models import AudioChunkOut, TTSInput
from server.thinker.pregenerator import UtterancePregenerator


class FakeTTSBackend(TTSBackend):
    name = "fake-tts"

    def __init__(self, audio: bytes = b"RIFF-fake") -> None:
        self.audio = audio
        self.inputs: list[TTSInput] = []

    async def synthesize(self, tts_input: TTSInput):
        self.inputs.append(tts_input)
        yield AudioChunkOut(data=self.audio, sequence=0, is_last=True)


class FailingTTSBackend(TTSBackend):
    name = "failing-tts"

    async def synthesize(self, tts_input: TTSInput):
        del tts_input
        raise RuntimeError("tts unavailable")
        yield AudioChunkOut(data=b"", sequence=0, is_last=True)


@pytest.mark.unit
async def test_pregenerator_promotes_high_priority_text_ready_candidate() -> None:
    now = datetime(2026, 5, 24, 23, 0, tzinfo=UTC)
    store = InMemoryCandidateStore()
    candidate = await store.insert_utterance_candidate(
        seed="優先",
        source="test",
        expires_at=now + timedelta(minutes=10),
        priority=0.9,
        maturity=1,
        generated_text="今これを言う。",
        created_at=now,
    )
    tts = FakeTTSBackend(audio=b"RIFF-cached")
    pregenerator = UtterancePregenerator(store=store, tts_backend=tts)

    result = await pregenerator.pregenerate_once(now=now)

    assert result.scanned_count == 1
    assert result.pregenerated_count == 1
    assert result.error_count == 0
    assert tts.inputs == [TTSInput(text="今これを言う。", style="neutral")]
    updated = store.utterance_candidates[0]
    assert updated.id == candidate.id
    assert updated.maturity == 2
    assert updated.generated_audio == b"RIFF-cached"


@pytest.mark.unit
async def test_pregenerator_skips_low_priority_and_seed_only_candidates() -> None:
    now = datetime(2026, 5, 24, 23, 0, tzinfo=UTC)
    store = InMemoryCandidateStore()
    await store.insert_utterance_candidate(
        seed="低優先",
        source="test",
        expires_at=now + timedelta(minutes=10),
        priority=0.5,
        maturity=1,
        generated_text="これはまだいい。",
        created_at=now,
    )
    await store.insert_utterance_candidate(
        seed="seed only",
        source="test",
        expires_at=now + timedelta(minutes=10),
        priority=1.0,
        maturity=0,
        generated_text=None,
        created_at=now,
    )
    tts = FakeTTSBackend()
    pregenerator = UtterancePregenerator(store=store, tts_backend=tts)

    result = await pregenerator.pregenerate_once(now=now)

    assert result.scanned_count == 2
    assert result.pregenerated_count == 0
    assert tts.inputs == []
    assert all(candidate.generated_audio is None for candidate in store.utterance_candidates)


@pytest.mark.unit
async def test_pregenerator_keeps_candidate_when_tts_fails() -> None:
    now = datetime(2026, 5, 24, 23, 0, tzinfo=UTC)
    store = InMemoryCandidateStore()
    await store.insert_utterance_candidate(
        seed="優先",
        source="test",
        expires_at=now + timedelta(minutes=10),
        priority=1.0,
        maturity=1,
        generated_text="これは失敗する。",
        created_at=now,
    )
    pregenerator = UtterancePregenerator(
        store=store,
        tts_backend=FailingTTSBackend(),
    )

    result = await pregenerator.pregenerate_once(now=now)

    assert result.pregenerated_count == 0
    assert result.error_count == 1
    assert store.utterance_candidates[0].maturity == 1
    assert store.utterance_candidates[0].generated_audio is None
