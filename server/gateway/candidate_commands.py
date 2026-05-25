from __future__ import annotations

import logging
from datetime import UTC, datetime

from server.gateway.initiative_feedback import (
    CandidateFeedbackStore,
    apply_feedback_to_metadata,
    feedback_scope_from_metadata,
)
from server.gateway.initiative_policy import (
    CandidateSpeakPolicy,
    DesireLoadAverages,
    InitiativeLLMJudge,
    SpeakabilityLoadAverages,
    metadata_from_utterance_candidate,
    safe_wait_decision,
)
from server.session import TomoroSession
from server.shared.candidate import CandidateStore
from server.shared.models import (
    CandidateSpeakDecision,
    PersonalityDynamics,
    SessionCommand,
    SessionEvent,
    SpeakabilityState,
    TomokoDesireState,
    TransitionResult,
)

logger = logging.getLogger(__name__)


def datetime_now_utc() -> datetime:
    return datetime.now(UTC)


class CandidateCommandRunner:
    def __init__(
        self,
        *,
        session: TomoroSession,
        store: CandidateStore,
        device_id: str | None,
        now_factory=datetime_now_utc,
        speak_policy: CandidateSpeakPolicy | None = None,
        desire_load: DesireLoadAverages | None = None,
        speakability_load: SpeakabilityLoadAverages | None = None,
        personality: PersonalityDynamics | None = None,
        feedback_store: CandidateFeedbackStore | None = None,
        llm_judge: InitiativeLLMJudge | None = None,
        presence_signal: float = 1.0,
    ) -> None:
        self.session = session
        self.store = store
        self.device_id = device_id
        self.now_factory = now_factory
        self.speak_policy = speak_policy or CandidateSpeakPolicy()
        self.desire_load = desire_load or DesireLoadAverages(
            now_factory=now_factory,
        )
        self.speakability_load = speakability_load or SpeakabilityLoadAverages(
            now_factory=now_factory,
        )
        self.personality = personality or PersonalityDynamics()
        self.feedback_store = feedback_store
        self.llm_judge = llm_judge
        self.presence_signal = max(0.0, min(1.0, presence_signal))

    async def run_result(self, result: TransitionResult) -> None:
        for command in result.commands:
            next_result = await self.run_command(command)
            if next_result is not None:
                await self.run_result(next_result)

    async def run_command(self, command: SessionCommand) -> TransitionResult | None:
        try:
            return await self._run_command(command)
        except Exception as exc:
            logger.warning(
                "candidate command failed type=%s reason=%s",
                command.type,
                exc,
            )
            return await self.session.post_event(
                SessionEvent(
                    type="candidate_command_failed",
                    payload={"command_type": command.type, "error": str(exc)},
                )
            )

    async def _run_command(self, command: SessionCommand) -> TransitionResult | None:
        now = self.now_factory()
        if command.type == "fetch_initiative_candidate":
            dismissed_count = await self.store.mark_expired_utterance_candidates(now)
            candidates = await self.store.fetch_active_utterance_candidates(
                now=now,
                limit=20,
            )
            selected, decision = await self._select_initiative_candidate(
                candidates,
                now=now,
            )
            logger.info(
                "initiative candidate fetched selected=%s active_count=%s "
                "dismissed_expired_count=%s policy_decision=%s score=%s "
                "reason=%s signals=%s",
                getattr(selected, "id", None),
                len(candidates),
                dismissed_count,
                getattr(decision, "decision", None),
                getattr(decision, "score", None),
                getattr(decision, "reason", None),
                getattr(decision, "signals", None),
            )
            return await self.session.post_event(
                SessionEvent(
                    type="initiative_candidate_loaded",
                    payload={
                        "candidate": selected,
                        "request_id": command.payload.get("request_id"),
                        "policy_decision": decision,
                    },
                    occurred_at=now,
                )
            )

        if command.type == "fetch_arrival_candidate":
            device_id = command.payload.get("device_id")
            if device_id is None:
                device_id = self.device_id
            candidate = await self.store.fetch_latest_fresh_arrival_candidate(
                now=now,
                device_id=str(device_id) if device_id is not None else None,
            )
            logger.info(
                "arrival candidate fetched selected=%s behavior=%s",
                getattr(candidate, "id", None),
                getattr(candidate, "behavior", None),
            )
            return await self.session.post_event(
                SessionEvent(
                    type="arrival_candidate_loaded",
                    payload={
                        "candidate": candidate,
                        "request_id": command.payload.get("request_id"),
                    },
                    occurred_at=now,
                )
            )

        if command.type == "mark_utterance_spoken":
            await self.store.mark_utterance_spoken(
                command.payload["candidate_id"],
                spoken_at=command.payload.get("spoken_at") or now,
            )
            return None

        if command.type == "dismiss_utterance_candidate":
            await self.store.dismiss_utterance_candidate(
                command.payload["candidate_id"],
                dismissed_at=command.payload.get("dismissed_at") or now,
            )
            return None

        if command.type == "mark_arrival_used":
            await self.store.mark_arrival_used(
                command.payload["arrival_candidate_id"],
                used_at=command.payload.get("used_at") or now,
            )
            return None

        if command.type in {"start_initiative_reply", "start_arrival_reply"}:
            await self.session.start_precomputed_reply(
                text=str(command.payload["text"]),
                device_id=self._command_device_id(command),
                reason=str(command.payload.get("reason") or "initiative"),
                audio_data=command.payload.get("generated_audio"),
                feedback_scope=command.payload.get("feedback_scope"),
            )
            return None

        if command.type == "judge_initiative_candidate":
            candidate = command.payload.get("candidate")
            if self.llm_judge is not None and candidate is not None:
                policy_decision = command.payload.get("policy_decision")
                desire = command.payload.get("desire")
                speakability = command.payload.get("speakability")
                if isinstance(policy_decision, CandidateSpeakDecision):
                    if desire is None and isinstance(
                        policy_decision.signals.get("desire"),
                        dict,
                    ):
                        desire = TomokoDesireState.from_json(
                            policy_decision.signals["desire"]
                        )
                    if speakability is None and isinstance(
                        policy_decision.signals.get("speakability"),
                        dict,
                    ):
                        speakability = SpeakabilityState.from_json(
                            policy_decision.signals["speakability"]
                        )
                if (
                    isinstance(policy_decision, CandidateSpeakDecision)
                    and isinstance(desire, TomokoDesireState)
                    and isinstance(speakability, SpeakabilityState)
                    and getattr(candidate, "generated_text", None) is not None
                ):
                    try:
                        judged = await self.llm_judge.judge(
                            candidate_text=str(candidate.generated_text),
                            candidate_reason=str(getattr(candidate, "seed", "")),
                            policy_decision=policy_decision,
                            desire=desire,
                            speakability=speakability,
                        )
                    except Exception as exc:
                        logger.info(
                            "initiative LLM judge failed candidate=%s reason=%s",
                            getattr(candidate, "id", None),
                            type(exc).__name__,
                        )
                        judged = safe_wait_decision("llm_judge_failed")
                    return await self.session.post_event(
                        SessionEvent(
                            type="initiative_candidate_loaded",
                            payload={
                                "candidate": candidate,
                                "request_id": command.payload.get("request_id"),
                                "policy_decision": judged,
                            },
                            occurred_at=now,
                        )
                    )
            logger.info(
                "initiative LLM judge fallback wait candidate=%s reason=not_configured",
                getattr(candidate, "id", None),
            )
            return await self.session.post_event(
                SessionEvent(
                    type="initiative_candidate_loaded",
                    payload={
                        "candidate": candidate,
                        "request_id": command.payload.get("request_id"),
                        "policy_decision": safe_wait_decision(
                            "llm_judge_not_configured"
                        ),
                    },
                    occurred_at=now,
                )
            )

        return None

    def _command_device_id(self, command: SessionCommand) -> str:
        device_id = command.payload.get("device_id") or self.device_id
        return str(device_id or "default")

    async def _select_initiative_candidate(
        self,
        candidates,
        *,
        now: datetime,
    ) -> tuple[object | None, CandidateSpeakDecision | None]:
        if not candidates:
            self.desire_load.apply(candidate_signal=0.0, personality=self.personality)
            self.speakability_load.apply(presence_signal=self._presence_signal())
            return None, None

        desire = self.desire_load.apply(
            candidate_signal=1.0,
            urgent_signal=1.0 if any(candidate.urgent for candidate in candidates) else 0.0,
            unspoken_signal=1.0
            if any(candidate.source in {"diary", "resume_unspoken"} for candidate in candidates)
            else 0.0,
            curiosity_signal=1.0
            if any(
                candidate.source in {"observation", "time_based"}
                or "question" in candidate.context_tags
                for candidate in candidates
            )
            else 0.0,
            attachment_signal=self._presence_signal(),
            playful_signal=1.0
            if any("playful" in candidate.context_tags for candidate in candidates)
            else 0.0,
            personality=self.personality,
        )
        base_speakability = self.speakability_load.apply(
            presence_signal=self._presence_signal(),
            activity_signal=self._presence_signal(),
        )
        decisions = []
        for candidate in candidates:
            metadata = metadata_from_utterance_candidate(candidate)
            feedback_summary = None
            if self.feedback_store is not None:
                feedback_summary = await self.feedback_store.summarize(
                    feedback_scope_from_metadata(metadata),
                    now=now,
                )
                metadata = apply_feedback_to_metadata(metadata, feedback_summary)
            speakability = base_speakability
            if feedback_summary is not None:
                speakability = SpeakabilityLoadAverages(
                    now_factory=self.now_factory,
                    initial_state=base_speakability,
                ).apply(
                    presence_signal=self._presence_signal(),
                    activity_signal=self._presence_signal(),
                    rejection_signal=feedback_summary.rejection_score,
                    acceptance_signal=feedback_summary.acceptance_score,
                    focus_signal=feedback_summary.intrusion_penalty,
                )
            decision = self.speak_policy.evaluate(
                desire=desire,
                speakability=speakability,
                personality=self.personality,
                candidate=metadata,
                now=now,
            )
            decisions.append((candidate, decision, metadata, desire, speakability))
        speakable = [
            item
            for item in decisions
            if item[1].decision in {"speak", "needs_llm_judge"}
        ]
        if not speakable:
            selected = max(decisions, key=lambda item: item[1].score)
        else:
            selected = max(
                speakable,
                key=lambda item: (
                    item[1].decision == "speak",
                    item[1].score,
                    item[0].maturity >= 2 and item[0].generated_audio is not None,
                    item[0].priority,
                ),
            )
        candidate, decision, metadata, selected_desire, selected_speakability = selected
        decision = CandidateSpeakDecision(
            decision=decision.decision,
            score=decision.score,
            threshold=decision.threshold,
            reason=decision.reason,
            signals={
                **decision.signals,
                "desire": selected_desire.to_json(),
                "speakability": selected_speakability.to_json(),
                "metadata": metadata.to_json(),
            },
        )
        return candidate, decision

    def _presence_signal(self) -> float:
        return self.presence_signal
