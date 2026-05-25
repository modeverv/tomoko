from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from server.shared.candidate import UtteranceCandidate
from server.shared.inference.router import InferenceRouter
from server.shared.models import (
    CandidateSpeakDecision,
    CandidateSpeakMetadata,
    LLMJudgeDecisionKind,
    PersonalityDynamics,
    SpeakabilityState,
    TomokoDesireState,
    TomoroRuntimeState,
)


def datetime_now_utc() -> datetime:
    return datetime.now(UTC)


class DesireLoadAverages:
    def __init__(
        self,
        *,
        now_factory: Callable[[], datetime] = datetime_now_utc,
        initial_state: TomokoDesireState | None = None,
    ) -> None:
        self.now_factory = now_factory
        self.state = initial_state or TomokoDesireState()
        self._updated_at = now_factory()

    def apply(
        self,
        *,
        candidate_signal: float = 0.0,
        urgent_signal: float = 0.0,
        unspoken_signal: float = 0.0,
        curiosity_signal: float = 0.0,
        attachment_signal: float = 0.0,
        playful_signal: float = 0.0,
        spoke_signal: float = 0.0,
        rejection_signal: float = 0.0,
        no_response_signal: float = 0.0,
        quiet_hours_signal: float = 0.0,
        personality: PersonalityDynamics | None = None,
    ) -> TomokoDesireState:
        now = self.now_factory()
        elapsed_sec = max(0.0, (now - self._updated_at).total_seconds())
        self._updated_at = now
        personality = personality or PersonalityDynamics()
        gain = 1.0 + (personality.talkativeness - 0.5) * 0.6
        decay = 1.0 + (personality.restraint - 0.5) * 0.4
        positive = (
            candidate_signal * 0.45
            + urgent_signal * 0.25
            + unspoken_signal * 0.2
            + curiosity_signal * (0.15 + personality.curiosity * 0.15)
            + attachment_signal * (0.12 + personality.attachment * 0.18)
            + playful_signal * (0.08 + personality.playfulness * 0.14)
        ) * gain
        negative = (
            spoke_signal * 0.55
            + rejection_signal * (0.65 + personality.sensitivity * 0.35)
            + no_response_signal * 0.25
            + quiet_hours_signal * 0.2
        ) * decay
        target = _clamp(positive - negative)
        self.state = TomokoDesireState(
            desire_1m=_ema(self.state.desire_1m, target, elapsed_sec, 60.0),
            desire_5m=_ema(self.state.desire_5m, target, elapsed_sec, 300.0),
            desire_30m=_ema(self.state.desire_30m, target, elapsed_sec, 1800.0),
            unspoken_pressure=_ema(
                self.state.unspoken_pressure,
                unspoken_signal,
                elapsed_sec,
                300.0,
            ),
            curiosity_pressure=_ema(
                self.state.curiosity_pressure,
                curiosity_signal,
                elapsed_sec,
                300.0,
            ),
            attachment_pressure=_ema(
                self.state.attachment_pressure,
                attachment_signal,
                elapsed_sec,
                300.0,
            ),
            playful_pressure=_ema(
                self.state.playful_pressure,
                playful_signal,
                elapsed_sec,
                300.0,
            ),
        )
        return self.state


class SpeakabilityLoadAverages:
    def __init__(
        self,
        *,
        now_factory: Callable[[], datetime] = datetime_now_utc,
        initial_state: SpeakabilityState | None = None,
    ) -> None:
        self.now_factory = now_factory
        self.state = initial_state or SpeakabilityState()
        self._updated_at = now_factory()

    def apply(
        self,
        *,
        presence_signal: float = 0.0,
        activity_signal: float = 0.0,
        conversation_signal: float = 0.0,
        focus_signal: float = 0.0,
        rejection_signal: float = 0.0,
        acceptance_signal: float = 0.0,
    ) -> SpeakabilityState:
        now = self.now_factory()
        elapsed_sec = max(0.0, (now - self._updated_at).total_seconds())
        self._updated_at = now
        rejection = _ema(self.state.recent_rejection_score, rejection_signal, elapsed_sec, 600.0)
        focus = _ema(self.state.focus_likelihood_5m, focus_signal, elapsed_sec, 300.0)
        self.state = SpeakabilityState(
            presence_1m=_ema(self.state.presence_1m, presence_signal, elapsed_sec, 60.0),
            presence_5m=_ema(self.state.presence_5m, presence_signal, elapsed_sec, 300.0),
            activity_1m=_ema(self.state.activity_1m, activity_signal, elapsed_sec, 60.0),
            activity_5m=_ema(self.state.activity_5m, activity_signal, elapsed_sec, 300.0),
            conversation_heat_1m=_ema(
                self.state.conversation_heat_1m,
                conversation_signal,
                elapsed_sec,
                60.0,
            ),
            conversation_heat_5m=_ema(
                self.state.conversation_heat_5m,
                conversation_signal,
                elapsed_sec,
                300.0,
            ),
            focus_likelihood_5m=focus,
            recent_rejection_score=rejection,
            recent_acceptance_score=_ema(
                self.state.recent_acceptance_score,
                acceptance_signal,
                elapsed_sec,
                600.0,
            ),
            intrusion_penalty=_clamp(rejection * 0.75 + focus * 0.25),
        )
        return self.state


class CandidateSpeakPolicy:
    def __init__(
        self,
        *,
        clear_speak_threshold: float = 0.68,
        clear_wait_threshold: float = 0.38,
    ) -> None:
        self.clear_speak_threshold = clear_speak_threshold
        self.clear_wait_threshold = clear_wait_threshold

    def evaluate(
        self,
        *,
        runtime: TomoroRuntimeState,
        desire: TomokoDesireState,
        speakability: SpeakabilityState,
        personality: PersonalityDynamics,
        candidate: CandidateSpeakMetadata,
        now: datetime | None = None,
    ) -> CandidateSpeakDecision:
        hard_gate_reason = self._hard_gate_reason(runtime, candidate, now=now)
        if hard_gate_reason is not None:
            return CandidateSpeakDecision(
                decision="wait",
                score=0.0,
                threshold=self.clear_speak_threshold,
                reason=hard_gate_reason,
                signals={"hard_gate": hard_gate_reason},
            )
        if candidate.feedback_penalty >= 0.75 and candidate.urgency < 0.8:
            return CandidateSpeakDecision(
                decision="wait",
                score=0.0,
                threshold=self.clear_speak_threshold,
                reason="feedback_scoped_rejection",
                signals={
                    "feedback_penalty": candidate.feedback_penalty,
                    "feedback_boost": candidate.feedback_boost,
                },
            )

        desire_score = (
            desire.desire_1m * 0.5
            + desire.desire_5m * 0.3
            + desire.desire_30m * 0.2
            + desire.unspoken_pressure * 0.1
            + desire.curiosity_pressure * 0.08
            + desire.attachment_pressure * 0.08
            + desire.playful_pressure * 0.05
        )
        speakability_score = (
            speakability.presence_1m * 0.22
            + speakability.presence_5m * 0.12
            + speakability.activity_1m * 0.1
            + speakability.conversation_heat_1m * 0.08
            + speakability.recent_acceptance_score * 0.16
            - speakability.focus_likelihood_5m * 0.18
            - speakability.recent_rejection_score * (0.18 + personality.sensitivity * 0.12)
            - speakability.intrusion_penalty * 0.24
        )
        candidate_score = (
            candidate.priority * 0.25
            + candidate.urgency * 0.22
            + candidate.emotional_need * 0.12
            + candidate.feedback_boost * 0.18
            - candidate.feedback_penalty * 0.3
            - candidate.intrusion_risk
            * (0.16 + speakability.focus_likelihood_5m * 0.2)
            + _source_weight(candidate.source)
            + _tag_weight(candidate.context_tags, personality)
        )
        personality_score = (
            (personality.talkativeness - 0.5) * 0.12
            - (personality.restraint - 0.5) * 0.14
            + personality.mood_talkativeness_1h * 0.08
            - personality.mood_restraint_1h * 0.08
            + personality.mood_curiosity_1h * _curiosity_candidate_factor(candidate) * 0.08
        )
        threshold = _clamp(
            self.clear_speak_threshold
            + (personality.restraint - 0.5) * 0.16
            + speakability.intrusion_penalty * 0.18
            + speakability.recent_rejection_score * personality.sensitivity * 0.12,
            minimum=0.45,
            maximum=0.9,
        )
        score = _clamp(
            desire_score + speakability_score + candidate_score + personality_score
        )
        signals = {
            "desire": round(desire_score, 4),
            "speakability": round(speakability_score, 4),
            "candidate": round(candidate_score, 4),
            "personality": round(personality_score, 4),
            "threshold": round(threshold, 4),
        }
        if score >= threshold:
            decision = "speak"
            reason = "score_above_threshold"
        elif score <= self.clear_wait_threshold:
            decision = "wait"
            reason = "score_below_wait_threshold"
        else:
            decision = "needs_llm_judge"
            reason = "score_in_llm_judge_band"
        return CandidateSpeakDecision(
            decision=decision,
            score=score,
            threshold=threshold,
            reason=reason,
            signals=signals,
        )

    def _hard_gate_reason(
        self,
        runtime: TomoroRuntimeState,
        candidate: CandidateSpeakMetadata,
        *,
        now: datetime | None,
    ) -> str | None:
        if runtime.attention_mode != "ambient":
            return "attention_not_ambient"
        if runtime.vad_state != "idle":
            return "vad_not_idle"
        if runtime.playback_state != "idle":
            return "playback_not_idle"
        if not runtime.output_state.audio_target_available:
            return "audio_target_unavailable"
        if not candidate.text_ready:
            return "candidate_not_text_ready"
        if candidate.expires_at is not None and (now or datetime_now_utc()) >= candidate.expires_at:
            return "candidate_expired"
        return None


class InitiativeLLMJudge:
    def __init__(self, router: InferenceRouter) -> None:
        self.router = router

    async def judge(
        self,
        *,
        candidate_text: str,
        candidate_reason: str | None,
        policy_decision: CandidateSpeakDecision,
        desire: TomokoDesireState,
        speakability: SpeakabilityState,
    ) -> CandidateSpeakDecision:
        backend = await self.router.select("candidate_gen", "privacy")
        prompt = build_llm_judge_prompt(
            candidate_text=candidate_text,
            candidate_reason=candidate_reason,
            policy_decision=policy_decision,
            desire=desire,
            speakability=speakability,
        )
        raw = "".join(
            [
                chunk
                async for chunk in backend.chat_stream(
                    _LLM_JUDGE_SYSTEM_PROMPT,
                    [{"role": "user", "content": prompt}],
                )
            ]
        )
        return decision_from_llm_judge_payload(_load_json_object(raw))


def metadata_from_utterance_candidate(
    candidate: UtteranceCandidate,
) -> CandidateSpeakMetadata:
    return CandidateSpeakMetadata(
        candidate_id=candidate.id,
        source=candidate.source,
        priority=candidate.priority,
        urgency=1.0 if candidate.urgent else _tag_float(candidate.context_tags, "urgency"),
        intrusion_risk=_tag_float(candidate.context_tags, "intrusion_risk"),
        emotional_need=_tag_float(candidate.context_tags, "emotional_need"),
        maturity=candidate.maturity,
        text_ready=candidate.generated_text is not None,
        audio_ready=candidate.generated_audio is not None,
        expires_at=candidate.expires_at,
        context_tags=candidate.context_tags,
        reason=candidate.seed,
    )


def safe_wait_decision(reason: str) -> CandidateSpeakDecision:
    return CandidateSpeakDecision(
        decision="wait",
        score=0.0,
        threshold=1.0,
        reason=reason,
        signals={"safe_fallback": True},
    )


def build_llm_judge_prompt(
    *,
    candidate_text: str,
    candidate_reason: str | None,
    policy_decision: CandidateSpeakDecision,
    desire: TomokoDesireState,
    speakability: SpeakabilityState,
) -> str:
    return "\n".join(
        [
            "Decide whether Tomoko should speak now.",
            "Return JSON only with this schema:",
            '{"decision":"speak_now|wait|defer","confidence":0.0,'
            '"reason":"short reason","tone":"soft","max_length":"short"}',
            f"candidate_text: {candidate_text}",
            f"candidate_reason: {candidate_reason or ''}",
            f"policy: {policy_decision.to_json()}",
            f"desire: {desire.to_json()}",
            f"speakability: {speakability.to_json()}",
        ]
    )


def decision_from_llm_judge_payload(payload: dict[str, object]) -> CandidateSpeakDecision:
    raw_decision = str(payload.get("decision", "wait"))
    if raw_decision not in {"speak_now", "wait", "defer"}:
        return safe_wait_decision("llm_judge_malformed")
    decision: LLMJudgeDecisionKind = raw_decision  # type: ignore[assignment]
    confidence = _clamp(float(payload.get("confidence", 0.0)))
    if decision == "speak_now" and confidence >= 0.55:
        return CandidateSpeakDecision(
            decision="speak",
            score=confidence,
            threshold=0.55,
            reason=str(payload.get("reason", "llm_judge_speak_now")),
            signals={
                "llm_judge": True,
                "tone": str(payload.get("tone", "soft")),
                "max_length": str(payload.get("max_length", "short")),
            },
        )
    return CandidateSpeakDecision(
        decision="wait",
        score=confidence,
        threshold=0.55,
        reason=str(payload.get("reason", f"llm_judge_{decision}")),
        signals={"llm_judge": True},
    )


_LLM_JUDGE_SYSTEM_PROMPT = """\
You are Tomoko's initiative timing judge.
Return JSON only. Never start new topics beyond the candidate.
Schema:
{
  "decision": "speak_now" | "wait" | "defer",
  "confidence": 0.0,
  "reason": "short reason",
  "tone": "soft",
  "max_length": "short"
}
"""


def _load_json_object(raw_text: str) -> dict[str, object]:
    stripped = raw_text.strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(stripped[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("LLM judge response must be a JSON object")
    return payload


def _ema(previous: float, target: float, elapsed_sec: float, window_sec: float) -> float:
    if elapsed_sec <= 0:
        return _clamp(target)
    alpha = 1.0 - math.exp(-elapsed_sec / window_sec)
    return _clamp(previous + (target - previous) * alpha)


def _source_weight(source: str) -> float:
    if source in {"diary", "journalist", "resume_unspoken"}:
        return 0.08
    if source in {"arrival", "presence"}:
        return 0.05
    if source in {"time_based", "observation"}:
        return 0.03
    return 0.0


def _tag_weight(tags: tuple[str, ...], personality: PersonalityDynamics) -> float:
    tag_set = set(tags)
    weight = 0.0
    if "observation" in tag_set or "question" in tag_set:
        weight += personality.curiosity * 0.06
    if "playful" in tag_set:
        weight += personality.playfulness * 0.06
    if "attachment" in tag_set:
        weight += personality.attachment * 0.06
    return weight


def _curiosity_candidate_factor(candidate: CandidateSpeakMetadata) -> float:
    if candidate.source in {"observation", "time_based"}:
        return 1.0
    if "question" in candidate.context_tags or "observation" in candidate.context_tags:
        return 1.0
    return 0.3


def _tag_float(tags: tuple[str, ...], prefix: str) -> float:
    needle = f"{prefix}:"
    for tag in tags:
        if tag.startswith(needle):
            with_value = tag.removeprefix(needle)
            try:
                return _clamp(float(with_value))
            except ValueError:
                return 0.0
    return 0.0


def _clamp(value: float, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return min(maximum, max(minimum, value))


def with_rejection_feedback(
    state: SpeakabilityState,
    *,
    score: float = 1.0,
) -> SpeakabilityState:
    score = _clamp(score)
    return replace(
        state,
        recent_rejection_score=_clamp(max(state.recent_rejection_score, score)),
        intrusion_penalty=_clamp(max(state.intrusion_penalty, score)),
    )
