from __future__ import annotations

from typing import Any

from server.shared.models import CandidateSpeakDecision, SessionEvent


def candidate_policy_payload(event: SessionEvent) -> dict[str, Any] | None:
    policy = event.payload.get("policy_decision")
    if isinstance(policy, CandidateSpeakDecision):
        return policy.to_json()
    return None
