from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from server.edge.pipeline.vad import VADProcessor
from server.session import TomoroSession, conversation_log_writes_output_lane
from server.shared.candidate import ArrivalCandidate, ArrivalContextSnapshot, UtteranceCandidate
from server.shared.models import CandidateSpeakDecision, ConnectedOutputState, SessionEvent


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


def _utterance_candidate(
    *,
    generated_text: str | None = "ねえ、少し休憩しない？",
    maturity: int = 1,
) -> UtteranceCandidate:
    now = datetime.now(UTC)
    return UtteranceCandidate(
        id="11111111-1111-1111-1111-111111111111",  # type: ignore[arg-type]
        seed="休憩を促す",
        generated_text=generated_text,
        generated_audio=None,
        priority=0.8,
        urgent=False,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        spoken_at=None,
        dismissed_at=None,
        maturity=maturity,  # type: ignore[arg-type]
        source="test",
        context_tags=(),
    )


def _arrival_candidate(
    *,
    behavior: str = "speak_first",
    utterance_text: str | None = "おかえり。今日は少し早かったね。",
) -> ArrivalCandidate:
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
        behavior=behavior,  # type: ignore[arg-type]
        utterance_text=utterance_text,
        utterance_audio=None,
        used_at=None,
    )


def _speak_decision() -> CandidateSpeakDecision:
    return CandidateSpeakDecision(
        decision="speak",
        score=1.0,
        threshold=0.5,
        reason="test_policy_speak",
    )


@pytest.mark.unit
async def test_idle_timer_fetches_initiative_candidate_only_when_speakable() -> None:
    session = _session()

    result = await session.post_event(SessionEvent(type="idle_timer_elapsed"))

    assert [command.type for command in result.commands] == [
        "fetch_initiative_candidate"
    ]

    session.state = "listening"
    blocked = await session.post_event(SessionEvent(type="idle_timer_elapsed"))

    assert blocked.commands == []
    assert blocked.emissions[0].payload["reason"] == "not_speakable"
    assert blocked.emissions[0].payload["gate_reason"] == "vad_not_idle"


@pytest.mark.unit
async def test_idle_timer_does_not_fetch_without_connected_audio_target() -> None:
    session = TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=lambda event: None,
    )

    result = await session.post_event(SessionEvent(type="idle_timer_elapsed"))

    assert result.commands == []
    assert result.emissions[0].payload["reason"] == "not_speakable"
    assert result.emissions[0].payload["gate_reason"] == "audio_target_unavailable"
    assert result.emissions[0].payload["audio_target_available"] is False


@pytest.mark.unit
async def test_session_started_fetches_arrival_candidate_only_when_speakable() -> None:
    session = _session()

    result = await session.post_event(
        SessionEvent(type="session_started", payload={"device_id": "desk"})
    )

    assert [command.type for command in result.commands] == ["fetch_arrival_candidate"]
    assert result.commands[0].payload["device_id"] == "desk"

    session.attention_mode = "withdrawn"
    blocked = await session.post_event(
        SessionEvent(type="session_started", payload={"device_id": "desk"})
    )

    assert blocked.commands == []
    assert blocked.emissions[0].payload["reason"] == "not_speakable"
    assert blocked.emissions[0].payload["gate_reason"] == "attention_not_ambient"


@pytest.mark.unit
async def test_loaded_initiative_candidate_starts_reply_and_marks_spoken() -> None:
    session = _session()
    candidate = _utterance_candidate()

    result = await session.post_event(
        SessionEvent(
            type="initiative_candidate_loaded",
            payload={"candidate": candidate},
        )
    )

    assert [command.type for command in result.commands] == [
        "start_initiative_reply",
        "mark_utterance_spoken",
    ]
    assert result.commands[0].payload["candidate_id"] == candidate.id
    assert result.commands[0].payload["output_lane"] == "initiative_turn"
    assert result.commands[0].payload["text"] == candidate.generated_text
    assert result.commands[0].payload["candidate_source"] == candidate.source
    assert result.commands[1].payload["candidate_id"] == candidate.id


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutate_session", "gate_reason"),
    [
        (lambda session: setattr(session, "attention_mode", "engaged"), "attention_not_ambient"),
        (lambda session: setattr(session, "state", "listening"), "vad_not_idle"),
    ],
)
async def test_loaded_initiative_candidate_final_gate_blocks_runtime_state(
    mutate_session,
    gate_reason: str,
) -> None:
    session = _session()
    mutate_session(session)

    result = await session.post_event(
        SessionEvent(
            type="initiative_candidate_loaded",
            payload={
                "candidate": _utterance_candidate(),
                "policy_decision": _speak_decision(),
            },
        )
    )

    assert result.commands == []
    assert result.emissions[0].payload["reason"] == "not_speakable"
    assert result.emissions[0].payload["gate_reason"] == gate_reason
    assert result.emissions[0].payload["output_lane"] == "initiative_turn"
    assert result.emissions[0].payload["floor_policy"] == "ambient_idle"


@pytest.mark.unit
async def test_loaded_initiative_candidate_final_gate_blocks_playback() -> None:
    session = _session()
    await session.post_event(
        SessionEvent(
            type="playback_started",
            payload={"turn_id": "turn-1", "chunk_id": 1},
        )
    )

    result = await session.post_event(
        SessionEvent(
            type="initiative_candidate_loaded",
            payload={
                "candidate": _utterance_candidate(),
                "policy_decision": _speak_decision(),
            },
        )
    )

    assert result.commands == []
    assert result.emissions[0].payload["reason"] == "not_speakable"
    assert result.emissions[0].payload["gate_reason"] == "playback_not_idle"


@pytest.mark.unit
async def test_loaded_initiative_candidate_final_gate_blocks_missing_audio_target() -> None:
    session = TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=lambda event: None,
    )

    result = await session.post_event(
        SessionEvent(
            type="initiative_candidate_loaded",
            payload={
                "candidate": _utterance_candidate(),
                "policy_decision": _speak_decision(),
            },
        )
    )

    assert result.commands == []
    assert result.emissions[0].payload["reason"] == "not_speakable"
    assert result.emissions[0].payload["gate_reason"] == "audio_target_unavailable"


@pytest.mark.unit
async def test_seed_only_initiative_candidate_is_dismissed() -> None:
    session = _session()
    candidate = _utterance_candidate(generated_text=None, maturity=0)

    result = await session.post_event(
        SessionEvent(
            type="initiative_candidate_loaded",
            payload={"candidate": candidate},
        )
    )

    assert [command.type for command in result.commands] == [
        "dismiss_utterance_candidate"
    ]
    assert result.commands[0].payload["candidate_id"] == candidate.id
    assert result.emissions[0].payload["reason"] == "not_text_ready"


@pytest.mark.unit
async def test_arrival_speak_first_starts_reply_and_marks_used() -> None:
    session = _session()
    candidate = _arrival_candidate()

    result = await session.post_event(
        SessionEvent(type="arrival_candidate_loaded", payload={"candidate": candidate})
    )

    assert [command.type for command in result.commands] == [
        "start_arrival_reply",
        "mark_arrival_used",
    ]
    assert result.commands[0].payload["arrival_candidate_id"] == candidate.id
    assert result.commands[0].payload["output_lane"] == "initiative_turn"
    assert result.commands[0].payload["text"] == candidate.utterance_text
    assert result.commands[0].payload["candidate_source"] == "arrival"
    assert result.commands[1].payload["arrival_candidate_id"] == candidate.id


@pytest.mark.unit
async def test_initiative_reply_does_not_start_conversation_session() -> None:
    class SessionStore:
        def __init__(self) -> None:
            self.created: list[str] = []

        async def create_session(self, *, device_id: str, start_reason: str):
            del device_id
            self.created.append(start_reason)
            return "33333333-3333-3333-3333-333333333333"

    store = SessionStore()
    session = TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=lambda event: None,
        connected_output_state=ConnectedOutputState.single_client(device_id="desk"),
        conversation_session_store=store,  # type: ignore[arg-type]
    )

    await session.start_precomputed_reply(
        text="さっきの話とは別で、ハードウェアの進化が少し気になってるんだ。",
        device_id="desk",
        reason="initiative",
        candidate_source="world_observation:abc",
        candidate_id="candidate-1",
    )

    assert store.created == []
    assert session.active_conversation_session_id is None


@pytest.mark.unit
def test_conversation_log_output_lane_policy_is_explicit() -> None:
    assert conversation_log_writes_output_lane("reply_turn") is True
    assert conversation_log_writes_output_lane("initiative_turn") is True
    assert conversation_log_writes_output_lane("interrupting_turn") is True
    assert conversation_log_writes_output_lane("gesture_audio") is False
    assert conversation_log_writes_output_lane("stop_ack") is False


@pytest.mark.unit
async def test_arrival_wait_silent_and_subtle_react_do_not_start_reply() -> None:
    session = _session()

    wait = await session.post_event(
        SessionEvent(
            type="arrival_candidate_loaded",
            payload={"candidate": _arrival_candidate(behavior="wait_silent")},
        )
    )
    subtle = await session.post_event(
        SessionEvent(
            type="arrival_candidate_loaded",
            payload={"candidate": _arrival_candidate(behavior="subtle_react")},
        )
    )

    assert [command.type for command in wait.commands] == ["mark_arrival_used"]
    assert [command.type for command in subtle.commands] == ["mark_arrival_used"]
    assert subtle.emissions[0].type == "arrival_subtle_react"


@pytest.mark.unit
async def test_arrival_speak_first_without_text_is_marked_used_but_not_spoken() -> None:
    session = _session()
    candidate = _arrival_candidate(utterance_text=None)

    result = await session.post_event(
        SessionEvent(type="arrival_candidate_loaded", payload={"candidate": candidate})
    )

    assert [command.type for command in result.commands] == ["mark_arrival_used"]
    assert result.emissions[0].payload["reason"] == "missing_utterance_text"
