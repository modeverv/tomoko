from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from server.session_candidate_policy_helpers import (
    candidate_policy_payload,
    candidate_policy_route,
    initiative_candidate_text_ready,
)
from server.shared.candidate import UtteranceCandidate
from server.shared.models import CandidateSpeakDecision, SessionEvent


@pytest.mark.unit
def test_candidate_policy_payload_preserves_decision_json_shape() -> None:
    decision = CandidateSpeakDecision(
        decision="wait",
        score=0.42,
        threshold=0.8,
        reason="too_intrusive",
        signals={"presence": 0.2},
    )
    event = SessionEvent(
        type="initiative_candidate_loaded",
        payload={"policy_decision": decision},
    )

    assert candidate_policy_payload(event) == {
        "schema_version": 1,
        "decision": "wait",
        "score": 0.42,
        "threshold": 0.8,
        "reason": "too_intrusive",
        "signals": {"presence": 0.2},
    }


@pytest.mark.unit
def test_candidate_policy_payload_ignores_non_decision_payload() -> None:
    event = SessionEvent(
        type="initiative_candidate_loaded",
        payload={"policy_decision": {"decision": "wait"}},
    )

    assert candidate_policy_payload(event) is None


def _candidate(
    *,
    generated_text: str | None = "ねえ、少し休憩しない？",
    generated_audio: bytes | None = None,
    maturity: int = 1,
) -> UtteranceCandidate:
    now = datetime.now(UTC)
    return UtteranceCandidate(
        id="11111111-1111-1111-1111-111111111111",  # type: ignore[arg-type]
        seed="休憩を促す",
        generated_text=generated_text,
        generated_audio=generated_audio,
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


@pytest.mark.unit
@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        (_candidate(generated_text="ねえ、少し休憩しない？", maturity=1), True),
        (
            _candidate(
                generated_text="ねえ、少し休憩しない？",
                generated_audio=b"wav",
                maturity=2,
            ),
            True,
        ),
        (_candidate(generated_text=None, maturity=0), False),
        (_candidate(generated_text=None, maturity=1), False),
    ],
)
def test_initiative_candidate_text_ready_preserves_session_condition(
    candidate: UtteranceCandidate,
    expected: bool,
) -> None:
    assert initiative_candidate_text_ready(candidate) is expected


@pytest.mark.unit
def test_candidate_policy_route_classifies_decision_without_payload_side_effects() -> None:
    assert (
        candidate_policy_route(
            CandidateSpeakDecision(
                decision="wait",
                score=0.1,
                threshold=0.5,
                reason="too_intrusive",
            )
        )
        == "wait"
    )
    assert (
        candidate_policy_route(
            CandidateSpeakDecision(
                decision="needs_llm_judge",
                score=0.51,
                threshold=0.5,
                reason="borderline",
            )
        )
        == "needs_llm_judge"
    )
    assert (
        candidate_policy_route(
            CandidateSpeakDecision(
                decision="speak",
                score=0.9,
                threshold=0.5,
                reason="clear",
            )
        )
        == "speak"
    )
    assert candidate_policy_route({"decision": "wait"}) == "speak"
