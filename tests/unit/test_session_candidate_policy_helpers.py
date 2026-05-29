from __future__ import annotations

import pytest

from server.session_candidate_policy_helpers import candidate_policy_payload
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
