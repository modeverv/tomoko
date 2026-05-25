from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from server.edge.pipeline.vad import VADProcessor
from server.gateway.candidate_commands import CandidateCommandRunner
from server.session import TomoroSession
from server.shared.candidate import ArrivalContextSnapshot, InMemoryCandidateStore
from server.shared.models import ConnectedOutputState, SessionEvent


class QuietVAD:
    def process_chunk(self, chunk: np.ndarray) -> float:
        del chunk
        return 0.0


class RecordingConversationSessionStore:
    def __init__(self) -> None:
        self.create_calls: list[tuple[str, str]] = []

    async def create_session(self, *, device_id: str, start_reason: str):
        self.create_calls.append((device_id, start_reason))
        raise AssertionError("initiative/arrival must not create conversation session")

    async def close_session(self, session_id, *, end_reason: str) -> None:
        del session_id, end_reason


def _session(
    events: list[dict[str, str]],
    audio_chunks: list[bytes] | None = None,
    conversation_session_store=None,
) -> TomoroSession:
    return TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=events.append,
        send_audio=audio_chunks.append if audio_chunks is not None else None,
        conversation_session_store=conversation_session_store,
        connected_output_state=ConnectedOutputState.single_client(device_id="desk"),
    )


@pytest.mark.unit
async def test_runner_fetches_initiative_candidate_speaks_and_marks_spoken() -> None:
    now = datetime(2026, 5, 24, 22, 30, tzinfo=UTC)
    store = InMemoryCandidateStore()
    candidate = await store.insert_utterance_candidate(
        seed="休憩",
        source="test",
        expires_at=now + timedelta(minutes=10),
        priority=0.9,
        maturity=1,
        generated_text="ねえ、少し休憩しない？",
        created_at=now,
    )
    events: list[dict[str, str]] = []
    session = _session(events)
    runner = CandidateCommandRunner(
        session=session,
        store=store,
        device_id="desk",
        now_factory=lambda: now,
    )

    result = await session.post_event(SessionEvent(type="idle_timer_elapsed"))
    await runner.run_result(result)

    assert events[-2:] == [
        {"type": "reply_text", "delta": "ねえ、少し休憩しない？"},
        {"type": "reply_done"},
    ]
    assert store.utterance_candidates[0].id == candidate.id
    assert store.utterance_candidates[0].spoken_at == now


@pytest.mark.unit
async def test_runner_fetches_arrival_candidate_speaks_and_marks_used() -> None:
    now = datetime(2026, 5, 24, 22, 30, tzinfo=UTC)
    store = InMemoryCandidateStore()
    candidate = await store.insert_arrival_candidate(
        context_snapshot=ArrivalContextSnapshot(
            computed_at=now,
            device_id="desk",
            local_time="22:30",
        ),
        behavior="speak_first",
        utterance_text="おかえり。今日は静かだったよ。",
        valid_until=now + timedelta(minutes=3),
        computed_at=now,
    )
    events: list[dict[str, str]] = []
    session = _session(events)
    runner = CandidateCommandRunner(
        session=session,
        store=store,
        device_id="desk",
        now_factory=lambda: now,
    )

    result = await session.post_event(
        SessionEvent(type="session_started", payload={"device_id": "desk"})
    )
    await runner.run_result(result)

    assert events[-2:] == [
        {"type": "reply_text", "delta": "おかえり。今日は静かだったよ。"},
        {"type": "reply_done"},
    ]
    assert store.arrival_candidates[0].id == candidate.id
    assert store.arrival_candidates[0].used_at == now


@pytest.mark.unit
async def test_initiative_and_arrival_do_not_start_conversation_session() -> None:
    now = datetime(2026, 5, 24, 22, 30, tzinfo=UTC)
    store = InMemoryCandidateStore()
    await store.insert_utterance_candidate(
        seed="休憩",
        source="test",
        expires_at=now + timedelta(minutes=10),
        priority=0.9,
        maturity=1,
        generated_text="ねえ、少し休憩しない？",
        created_at=now,
    )
    events: list[dict[str, str]] = []
    conversation_sessions = RecordingConversationSessionStore()
    session = _session(
        events,
        conversation_session_store=conversation_sessions,
    )
    runner = CandidateCommandRunner(
        session=session,
        store=store,
        device_id="desk",
        now_factory=lambda: now,
    )

    result = await session.post_event(SessionEvent(type="idle_timer_elapsed"))
    await runner.run_result(result)

    assert conversation_sessions.create_calls == []
    assert session.active_conversation_session_id is None
    assert session.attention_mode == "engaged"


@pytest.mark.unit
async def test_runner_uses_pregenerated_audio_without_tts() -> None:
    now = datetime(2026, 5, 24, 22, 30, tzinfo=UTC)
    audio = b"RIFF\x24\x00\x00\x00WAVEfmt "
    store = InMemoryCandidateStore()
    await store.insert_utterance_candidate(
        seed="即再生",
        source="test",
        expires_at=now + timedelta(minutes=10),
        priority=1.0,
        maturity=2,
        generated_text="今ならすぐ言えるよ。",
        generated_audio=audio,
        created_at=now,
    )
    events: list[dict[str, str]] = []
    audio_chunks: list[bytes] = []
    session = _session(events, audio_chunks)
    runner = CandidateCommandRunner(
        session=session,
        store=store,
        device_id="desk",
        now_factory=lambda: now,
    )

    result = await session.post_event(SessionEvent(type="idle_timer_elapsed"))
    await runner.run_result(result)

    assert events[-4:] == [
        {"type": "reply_text", "delta": "今ならすぐ言えるよ。"},
        {"type": "audio_start", "turn_id": events[-3]["turn_id"]},
        {"type": "audio_end", "turn_id": events[-3]["turn_id"]},
        {"type": "reply_done"},
    ]
    assert audio_chunks == [audio]


@pytest.mark.unit
async def test_runner_prefers_pregenerated_audio_candidate() -> None:
    now = datetime(2026, 5, 24, 22, 30, tzinfo=UTC)
    cached_audio = b"RIFF\x24\x00\x00\x00WAVEfmt cached"
    store = InMemoryCandidateStore()
    await store.insert_utterance_candidate(
        seed="高優先テキスト",
        source="test",
        expires_at=now + timedelta(minutes=10),
        priority=1.0,
        maturity=1,
        generated_text="これはTTSが必要。",
        created_at=now,
    )
    cached = await store.insert_utterance_candidate(
        seed="少し低いが事前生成済み",
        source="test",
        expires_at=now + timedelta(minutes=10),
        priority=0.7,
        maturity=2,
        generated_text="これはすぐ再生できる。",
        generated_audio=cached_audio,
        created_at=now + timedelta(seconds=1),
    )
    events: list[dict[str, str]] = []
    audio_chunks: list[bytes] = []
    session = _session(events, audio_chunks)
    runner = CandidateCommandRunner(
        session=session,
        store=store,
        device_id="desk",
        now_factory=lambda: now,
    )

    result = await session.post_event(SessionEvent(type="idle_timer_elapsed"))
    await runner.run_result(result)

    assert {"type": "reply_text", "delta": "これはすぐ再生できる。"} in events
    assert audio_chunks == [cached_audio]
    assert next(
        candidate for candidate in store.utterance_candidates if candidate.id == cached.id
    ).spoken_at == now
