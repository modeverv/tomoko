from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from server.edge.pipeline.vad import VADProcessor
from server.session import TomoroSession
from server.shared.candidate import UtteranceCandidate
from server.shared.models import SessionEvent, TransitionResult


class QuietVAD:
    def process_chunk(self, chunk: np.ndarray) -> float:
        del chunk
        return 0.0


def _session() -> TomoroSession:
    return TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=lambda event: None,
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
