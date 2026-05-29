from __future__ import annotations

from typing import Any, Literal

from server.shared.candidate import UtteranceCandidate
from server.shared.models import CandidateSpeakDecision, SessionEvent

CandidatePolicyRoute = Literal["wait", "needs_llm_judge", "speak"]


def candidate_policy_payload(event: SessionEvent) -> dict[str, Any] | None:
    policy = event.payload.get("policy_decision")
    if isinstance(policy, CandidateSpeakDecision):
        return policy.to_json()
    return None


def initiative_candidate_text_ready(candidate: UtteranceCandidate) -> bool:
    return candidate.maturity >= 1 and candidate.generated_text is not None


def candidate_policy_route(policy_decision: object) -> CandidatePolicyRoute:
    if not isinstance(policy_decision, CandidateSpeakDecision):
        return "speak"
    if policy_decision.decision == "wait":
        return "wait"
    if policy_decision.decision == "needs_llm_judge":
        return "needs_llm_judge"
    return "speak"
