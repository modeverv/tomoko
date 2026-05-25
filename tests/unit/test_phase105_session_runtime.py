from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from server.edge.participation.wake_word import WakeWordJudge
from server.edge.pipeline.vad import VADProcessor
from server.gateway.turn_taking.barge_in import BargeInDetector
from server.session import TomoroSession
from server.shared.candidate import (
    ArrivalCandidate,
    ArrivalContextSnapshot,
    UtteranceCandidate,
)
from server.shared.models import ConnectedOutputState, SessionEvent, Transcript, TransitionResult


class QuietVAD:
    def process_chunk(self, chunk: np.ndarray) -> float:
        del chunk
        return 0.0


def _session() -> TomoroSession:
    return TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=lambda event: None,
        connected_output_state=ConnectedOutputState.single_client(device_id="desk"),
    )


def _session_with_participation() -> TomoroSession:
    return TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=lambda event: None,
        participation_judge=WakeWordJudge(),
        connected_output_state=ConnectedOutputState.single_client(device_id="desk"),
    )


def _utterance_candidate() -> UtteranceCandidate:
    now = datetime.now(UTC)
    return UtteranceCandidate(
        id="11111111-1111-1111-1111-111111111111",  # type: ignore[arg-type]
        seed="休憩を促す",
        generated_text="ねえ、少し休憩しない？",
        generated_audio=None,
        priority=0.8,
        urgent=False,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        spoken_at=None,
        dismissed_at=None,
        maturity=1,
        source="test",
        context_tags=(),
    )


def _arrival_candidate() -> ArrivalCandidate:
    now = datetime.now(UTC)
    return ArrivalCandidate(
        id="22222222-2222-2222-2222-222222222222",  # type: ignore[arg-type]
        computed_at=now,
        valid_until=now + timedelta(minutes=3),
        context_snapshot=ArrivalContextSnapshot(
            computed_at=now,
            device_id="desk",
            local_time="22:00",
        ),
        behavior="speak_first",
        utterance_text="おかえり。今日は少し早かったね。",
        utterance_audio=None,
        used_at=None,
    )


def _transcript(text: str) -> Transcript:
    return Transcript(
        text=text,
        device_id="desk",
        speaker=None,
        audio_level_db=-20.0,
        recorded_at=datetime.now(UTC),
        is_final=True,
    )


@pytest.mark.unit
async def test_post_event_drains_events_sequentially_when_called_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    order: list[tuple[str, str]] = []
    original_process_event = session._process_event

    async def slow_process_event(event: SessionEvent) -> TransitionResult:
        order.append(("start", event.type))
        await asyncio.sleep(0.01)
        result = await original_process_event(event)
        order.append(("end", event.type))
        return result

    monkeypatch.setattr(session, "_process_event", slow_process_event)

    await asyncio.gather(
        session.post_event(SessionEvent(type="idle_timer_elapsed")),
        session.post_event(SessionEvent(type="session_started")),
    )

    assert order == [
        ("start", "idle_timer_elapsed"),
        ("end", "idle_timer_elapsed"),
        ("start", "session_started"),
        ("end", "session_started"),
    ]


@pytest.mark.unit
async def test_candidate_fetch_command_carries_request_id_for_stale_results() -> None:
    session = _session()

    first = await session.post_event(SessionEvent(type="idle_timer_elapsed"))
    second = await session.post_event(SessionEvent(type="idle_timer_elapsed"))

    assert first.commands[0].payload["request_id"] != second.commands[0].payload[
        "request_id"
    ]


@pytest.mark.unit
async def test_connected_output_state_updates_runtime_snapshot() -> None:
    session = TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=lambda event: None,
    )
    output_state = ConnectedOutputState.single_client(device_id="kitchen")

    result = await session.post_event(
        SessionEvent(
            type="connected_output_state_changed",
            payload={"output_state": output_state},
        )
    )

    assert result.state.output_state == output_state
    assert session.get_now_state().output_state.active_device_id == "kitchen"
    assert result.emissions[0].payload["audio_target_available"] is True


@pytest.mark.unit
async def test_stale_initiative_candidate_loaded_is_ignored() -> None:
    session = _session()
    first = await session.post_event(SessionEvent(type="idle_timer_elapsed"))
    stale_request_id = first.commands[0].payload["request_id"]
    await session.post_event(SessionEvent(type="idle_timer_elapsed"))

    result = await session.post_event(
        SessionEvent(
            type="initiative_candidate_loaded",
            payload={
                "candidate": _utterance_candidate(),
                "request_id": stale_request_id,
            },
        )
    )

    assert result.commands == []
    assert result.emissions[0].type == "initiative_skipped"
    assert result.emissions[0].payload["reason"] == "stale_result"


@pytest.mark.unit
async def test_stale_arrival_candidate_loaded_is_ignored() -> None:
    session = _session()
    first = await session.post_event(SessionEvent(type="session_started"))
    stale_request_id = first.commands[0].payload["request_id"]
    await session.post_event(SessionEvent(type="session_started"))

    result = await session.post_event(
        SessionEvent(
            type="arrival_candidate_loaded",
            payload={
                "candidate": None,
                "request_id": stale_request_id,
            },
        )
    )

    assert result.commands == []
    assert result.emissions[0].type == "arrival_skipped"
    assert result.emissions[0].payload["reason"] == "stale_result"


@pytest.mark.unit
async def test_human_attention_blocks_late_initiative_candidate_result() -> None:
    session = _session()
    result = await session.post_event(SessionEvent(type="idle_timer_elapsed"))
    request_id = result.commands[0].payload["request_id"]
    session.attention_mode = "engaged"

    loaded = await session.post_event(
        SessionEvent(
            type="initiative_candidate_loaded",
            payload={
                "candidate": _utterance_candidate(),
                "request_id": request_id,
            },
        )
    )

    assert loaded.commands == []
    assert loaded.emissions[0].payload["reason"] == "not_speakable"
    assert loaded.emissions[0].payload["gate_reason"] == "attention_not_ambient"


@pytest.mark.unit
async def test_start_reason_state_records_wake_word_and_followup() -> None:
    session = _session_with_participation()

    await session.process_transcript(_transcript("トモコ、少し聞いて"))

    assert session.get_now_state().last_start_reason == "wake_word"

    await session.process_transcript(_transcript("さっきの続きなんだけど"))

    assert session.get_now_state().last_start_reason == "followup"


@pytest.mark.unit
async def test_initiative_and_arrival_commands_carry_start_reason() -> None:
    session = _session()
    initiative = _utterance_candidate()
    arrival = _arrival_candidate()

    initiative_result = await session.post_event(
        SessionEvent(
            type="initiative_candidate_loaded",
            payload={"candidate": initiative},
        )
    )
    arrival_result = await session.post_event(
        SessionEvent(
            type="arrival_candidate_loaded",
            payload={"candidate": arrival},
        )
    )

    assert initiative_result.commands[0].payload["start_reason"] == "initiative"
    assert initiative_result.commands[0].payload["started_by"] == "initiative"
    assert arrival_result.commands[0].payload["start_reason"] == "arrival"
    assert arrival_result.commands[0].payload["started_by"] == "arrival"
    assert session.get_now_state().last_start_reason == "arrival"


@pytest.mark.unit
async def test_withdrawn_priority_blocks_followup_and_initiative() -> None:
    session = _session_with_participation()
    session.attention_mode = "withdrawn"

    await session.process_transcript(_transcript("さっきの続きなんだけど"))
    initiative = await session.post_event(SessionEvent(type="idle_timer_elapsed"))

    assert session.get_now_state().last_start_reason is None
    assert initiative.commands == []
    assert initiative.emissions[0].payload["reason"] == "not_speakable"


@pytest.mark.unit
async def test_hard_interrupt_priority_beats_active_playback_echo() -> None:
    session = TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=lambda event: None,
        barge_in_detector=BargeInDetector(),
    )
    await session.post_event(
        SessionEvent(
            type="playback_started",
            payload={"turn_id": "turn-1", "chunk_id": 1},
        )
    )

    result = session._reduce(
        SessionEvent(
            type="transcript_finalized",
            payload={"transcript": _transcript("違う違う、待って")},
        )
    )

    assert result.emissions[0].payload["kind"] == "hard_interrupt"
    assert result.emissions[0].payload["action"] == "restart_turn"
