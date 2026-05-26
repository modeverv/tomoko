from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import numpy as np
import pytest

from server.edge.pipeline.vad import VADProcessor
from server.gateway.candidate_commands import CandidateCommandRunner
from server.gateway.initiative_feedback import (
    CandidateFeedbackScope,
    CandidateFeedbackSignal,
    InMemoryCandidateFeedbackStore,
    classify_feedback,
)
from server.gateway.initiative_policy import (
    CandidateSpeakPolicy,
    DesireLoadAverages,
    InitiativeLLMJudge,
    SpeakabilityLoadAverages,
    build_llm_judge_prompt,
    decision_from_llm_judge_payload,
    metadata_from_utterance_candidate,
    with_rejection_feedback,
)
from server.session import TomoroSession
from server.shared.candidate import InMemoryCandidateStore, UtteranceCandidate
from server.shared.models import (
    CandidateSpeakDecision,
    CandidateSpeakMetadata,
    ConnectedOutputState,
    PersonalityDynamics,
    SessionEvent,
    SpeakabilityState,
    TomokoDesireState,
    Transcript,
)


class QuietVAD:
    def process_chunk(self, chunk: np.ndarray) -> float:
        del chunk
        return 0.0


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    def __call__(self) -> datetime:
        return self.now


class AlwaysWaitPolicy(CandidateSpeakPolicy):
    def evaluate(self, **kwargs: Any) -> CandidateSpeakDecision:
        del kwargs
        return CandidateSpeakDecision(
            decision="wait",
            score=0.1,
            threshold=0.9,
            reason="test_wait",
        )


class NeedsJudgePolicy(CandidateSpeakPolicy):
    def evaluate(self, **kwargs: Any) -> CandidateSpeakDecision:
        del kwargs
        return CandidateSpeakDecision(
            decision="needs_llm_judge",
            score=0.5,
            threshold=0.7,
            reason="test_boundary",
        )


class FakeBackend:
    name = "fake"
    privacy_allowed = True

    def __init__(self, chunks: tuple[str, ...]) -> None:
        self.chunks = chunks
        self.requests: list[tuple[str, list[dict[str, str]]]] = []

    async def chat_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
    ) -> AsyncGenerator[str, None]:
        self.requests.append((system_prompt, messages))
        for chunk in self.chunks:
            yield chunk


class FakeRouter:
    def __init__(self, backend: FakeBackend) -> None:
        self.backend = backend
        self.selections: list[tuple[str, str]] = []

    async def select(self, role: str, preference: str = "latency") -> FakeBackend:
        self.selections.append((role, preference))
        return self.backend


def _session() -> TomoroSession:
    return TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=lambda event: None,
        connected_output_state=ConnectedOutputState.single_client(device_id="desk"),
    )


def _candidate(
    *,
    priority: float = 0.7,
    urgent: bool = False,
    source: str = "time_based",
    context_tags: tuple[str, ...] = (),
    generated_text: str = "ねえ、少し休憩しない？",
) -> UtteranceCandidate:
    now = datetime.now(UTC)
    return UtteranceCandidate(
        id=uuid4(),
        seed="少し休憩しない？",
        generated_text=generated_text,
        generated_audio=None,
        priority=priority,
        urgent=urgent,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        spoken_at=None,
        dismissed_at=None,
        maturity=1,
        source=source,
        context_tags=context_tags,
    )


@pytest.mark.unit
def test_phase106_dto_json_round_trips() -> None:
    desire = TomokoDesireState(
        desire_1m=0.1,
        desire_5m=0.2,
        desire_30m=0.3,
        unspoken_pressure=0.4,
        curiosity_pressure=0.5,
        attachment_pressure=0.6,
        playful_pressure=0.7,
    )
    speakability = SpeakabilityState(
        presence_1m=0.8,
        presence_5m=0.7,
        activity_1m=0.6,
        activity_5m=0.5,
        conversation_heat_1m=0.4,
        conversation_heat_5m=0.3,
        focus_likelihood_5m=0.2,
        recent_rejection_score=0.1,
        recent_acceptance_score=0.9,
        intrusion_penalty=0.05,
    )
    personality = PersonalityDynamics(
        talkativeness=0.9,
        restraint=0.2,
        curiosity=0.8,
        attachment=0.7,
        sensitivity=0.3,
        playfulness=0.6,
        mood_talkativeness_1h=0.2,
        mood_restraint_1h=-0.1,
        mood_curiosity_1h=0.15,
    )
    metadata = CandidateSpeakMetadata(
        candidate_id=uuid4(),
        source="diary",
        priority=0.8,
        urgency=0.4,
        intrusion_risk=0.1,
        emotional_need=0.6,
        maturity=1,
        text_ready=True,
        expires_at=datetime(2026, 5, 25, 13, 0, tzinfo=UTC),
        context_tags=("question",),
        reason="言えなかったこと",
    )
    decision = CandidateSpeakDecision(
        decision="needs_llm_judge",
        score=0.5,
        threshold=0.6,
        reason="boundary",
        signals={"desire": 0.3},
    )

    assert TomokoDesireState.from_json(desire.to_json()) == desire
    assert SpeakabilityState.from_json(speakability.to_json()) == speakability
    assert PersonalityDynamics.from_json(personality.to_json()) == personality
    assert CandidateSpeakMetadata.from_json(metadata.to_json()) == metadata
    assert CandidateSpeakDecision.from_json(decision.to_json()) == decision


@pytest.mark.unit
def test_phase106_load_averages_react_at_different_speeds() -> None:
    clock = Clock(datetime(2026, 5, 25, 12, 0, tzinfo=UTC))
    load = DesireLoadAverages(now_factory=clock)

    clock.advance(60)
    state = load.apply(candidate_signal=1.0)

    assert state.desire_1m > state.desire_5m
    assert state.desire_5m > state.desire_30m


@pytest.mark.unit
def test_phase106_rejection_feedback_raises_and_decays_penalty() -> None:
    clock = Clock(datetime(2026, 5, 25, 12, 0, tzinfo=UTC))
    load = SpeakabilityLoadAverages(
        now_factory=clock,
        initial_state=with_rejection_feedback(SpeakabilityState(), score=0.9),
    )

    clock.advance(60)
    state = load.apply(rejection_signal=0.0)

    assert 0.0 < state.intrusion_penalty < 0.9


@pytest.mark.unit
def test_phase106_personality_changes_policy_score() -> None:
    desire = TomokoDesireState(desire_1m=0.7, desire_5m=0.5, desire_30m=0.3)
    speakability = SpeakabilityState(presence_1m=1.0, presence_5m=0.8)
    candidate = metadata_from_utterance_candidate(_candidate())
    policy = CandidateSpeakPolicy()

    quiet = policy.evaluate(
        desire=desire,
        speakability=speakability,
        personality=PersonalityDynamics(talkativeness=0.1, restraint=0.9),
        candidate=candidate,
    )
    chatty = policy.evaluate(
        desire=desire,
        speakability=speakability,
        personality=PersonalityDynamics(talkativeness=0.9, restraint=0.1),
        candidate=candidate,
    )

    assert chatty.score > quiet.score


@pytest.mark.unit
def test_phase106_policy_is_soft_and_does_not_return_runtime_gate_reasons() -> None:
    decision = CandidateSpeakPolicy().evaluate(
        desire=TomokoDesireState(desire_1m=1.0, desire_5m=1.0, desire_30m=1.0),
        speakability=SpeakabilityState(presence_1m=1.0),
        personality=PersonalityDynamics(talkativeness=1.0),
        candidate=metadata_from_utterance_candidate(_candidate(urgent=True)),
    )

    assert decision.decision in {"speak", "needs_llm_judge"}
    assert decision.reason not in {
        "attention_not_ambient",
        "vad_not_idle",
        "playback_not_idle",
        "audio_target_unavailable",
    }


@pytest.mark.unit
def test_phase106_policy_uses_candidate_metadata() -> None:
    desire = TomokoDesireState(desire_1m=0.55, desire_5m=0.4, desire_30m=0.25)
    speakability = SpeakabilityState(
        presence_1m=1.0,
        focus_likelihood_5m=0.8,
        intrusion_penalty=0.1,
    )
    personality = PersonalityDynamics()
    policy = CandidateSpeakPolicy()

    safe = policy.evaluate(
        desire=desire,
        speakability=speakability,
        personality=personality,
        candidate=metadata_from_utterance_candidate(
            _candidate(priority=0.8, context_tags=("intrusion_risk:0.0",))
        ),
    )
    intrusive = policy.evaluate(
        desire=desire,
        speakability=speakability,
        personality=personality,
        candidate=metadata_from_utterance_candidate(
            _candidate(priority=0.8, context_tags=("intrusion_risk:1.0",))
        ),
    )

    assert safe.score > intrusive.score


@pytest.mark.unit
def test_phase1010_policy_penalizes_unbridged_topic_shift_after_heavy_context() -> None:
    desire = TomokoDesireState(desire_1m=0.75, desire_5m=0.5, desire_30m=0.3)
    speakability = SpeakabilityState(presence_1m=1.0, presence_5m=0.8)
    personality = PersonalityDynamics()
    policy = CandidateSpeakPolicy()
    tags = ("recent_heavy_conversation", "topic_shift_bridge_required")

    bridged = policy.evaluate(
        desire=desire,
        speakability=speakability,
        personality=personality,
        candidate=metadata_from_utterance_candidate(
            _candidate(
                priority=0.8,
                source="world_observation:abc",
                context_tags=tags,
                generated_text="さっきの話とは別で、ハードウェアの進化が少し気になるんだ。",
            )
        ),
    )
    abrupt = policy.evaluate(
        desire=desire,
        speakability=speakability,
        personality=personality,
        candidate=metadata_from_utterance_candidate(
            _candidate(
                priority=0.8,
                source="world_observation:abc",
                context_tags=tags,
                generated_text="ハードウェアの進化が少し気になるんだ。",
            )
        ),
    )

    assert bridged.score > abrupt.score


@pytest.mark.unit
def test_phase106_llm_judge_prompt_and_parser_are_schema_shaped() -> None:
    policy_decision = CandidateSpeakDecision(
        decision="needs_llm_judge",
        score=0.51,
        threshold=0.68,
        reason="boundary",
    )
    prompt = build_llm_judge_prompt(
        candidate_text="ねえ、少し休憩しない？",
        candidate_reason="長く作業している",
        policy_decision=policy_decision,
        desire=TomokoDesireState(desire_1m=0.7),
        speakability=SpeakabilityState(presence_1m=1.0),
    )

    assert '"decision":"speak_now|wait|defer"' in prompt

    speak = decision_from_llm_judge_payload(
        {
            "decision": "speak_now",
            "confidence": 0.7,
            "reason": "short and welcome",
            "tone": "soft",
            "max_length": "short",
        }
    )
    malformed = decision_from_llm_judge_payload({"decision": "maybe"})

    assert speak.decision == "speak"
    assert malformed.decision == "wait"
    assert malformed.reason == "llm_judge_malformed"


@pytest.mark.unit
async def test_phase106_runner_sends_policy_decision_to_session() -> None:
    now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    store = InMemoryCandidateStore()
    candidate = await store.insert_utterance_candidate(
        seed="今じゃない話題",
        source="time_based",
        expires_at=now + timedelta(minutes=10),
        priority=0.1,
        generated_text="少しだけ話してもいい？",
        maturity=1,
        created_at=now,
        context_tags=("intrusion_risk:1.0",),
    )
    runner = CandidateCommandRunner(
        session=_session(),
        store=store,
        device_id="desk",
        now_factory=lambda: now,
        speak_policy=AlwaysWaitPolicy(),
    )

    result = await runner.run_command(
        (await runner.session.post_event(SessionEvent(type="idle_timer_elapsed"))).commands[
            0
        ]
    )

    assert result is not None
    assert result.emissions[0].payload["reason"] == "policy_wait"
    assert result.emissions[0].payload["candidate_id"] == candidate.id


@pytest.mark.unit
async def test_phase106_llm_judge_boundary_falls_back_to_wait_safely() -> None:
    now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    store = InMemoryCandidateStore()
    candidate = await store.insert_utterance_candidate(
        seed="境界ケース",
        source="time_based",
        expires_at=now + timedelta(minutes=10),
        priority=0.7,
        generated_text="短く声をかけるね。",
        maturity=1,
        created_at=now,
    )
    runner = CandidateCommandRunner(
        session=_session(),
        store=store,
        device_id="desk",
        now_factory=lambda: now,
        speak_policy=NeedsJudgePolicy(),
    )
    first = await runner.session.post_event(SessionEvent(type="idle_timer_elapsed"))
    loaded = await runner.run_command(first.commands[0])

    assert loaded is not None
    assert loaded.emissions[0].type == "initiative_llm_judge_requested"

    final = await runner.run_command(loaded.commands[0])

    assert final is not None
    assert final.emissions[0].payload["reason"] == "policy_wait"
    assert final.emissions[0].payload["candidate_id"] == candidate.id
    assert final.emissions[0].payload["policy"]["reason"] == "llm_judge_not_configured"


@pytest.mark.unit
async def test_phase106_feedback_is_scoped_by_topic_and_changes_selection() -> None:
    now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    store = InMemoryCandidateStore()
    rejected = await store.insert_utterance_candidate(
        seed="洗濯の話",
        source="diary",
        expires_at=now + timedelta(minutes=10),
        priority=0.7,
        generated_text="洗濯物の話、今する？",
        maturity=1,
        created_at=now,
        context_tags=("topic:laundry", "emotional_need:0.8"),
    )
    accepted = await store.insert_utterance_candidate(
        seed="ごはんの話",
        source="time_based",
        expires_at=now + timedelta(minutes=10),
        priority=0.7,
        generated_text="ごはん、あとで少し相談してもいい？",
        maturity=1,
        created_at=now + timedelta(seconds=1),
        context_tags=("topic:food", "emotional_need:0.8"),
    )
    feedback = InMemoryCandidateFeedbackStore()
    await feedback.record(
        CandidateFeedbackSignal(
            scope=CandidateFeedbackScope(
                candidate_id=rejected.id,
                source="diary",
                topic="laundry",
                emotional_need="high",
            ),
            kind="rejection",
            score=1.0,
            observed_at=now,
            transcript_text="それ今じゃない",
        )
    )
    runner = CandidateCommandRunner(
        session=_session(),
        store=store,
        device_id="desk",
        now_factory=lambda: now,
        feedback_store=feedback,
    )

    selected, decision = await runner._select_initiative_candidate(  # noqa: SLF001
        [rejected, accepted],
        now=now,
    )

    assert selected == accepted
    assert decision is not None
    assert decision.signals["metadata"]["feedback_penalty"] < 0.75


@pytest.mark.unit
async def test_phase106_session_records_rejection_feedback_for_active_candidate() -> None:
    now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    feedback = InMemoryCandidateFeedbackStore()
    session = TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=lambda event: None,
        connected_output_state=ConnectedOutputState.single_client(device_id="desk"),
        candidate_feedback_store=feedback,
    )
    scope = CandidateFeedbackScope(
        source="diary",
        topic="laundry",
        emotional_need="high",
    )
    await session.start_precomputed_reply(
        text="洗濯物の話、今する？",
        device_id="desk",
        reason="initiative",
        feedback_scope=scope,
    )
    await session.process_transcript(
        Transcript(
            text="それ今じゃない",
            device_id="desk",
            speaker="user",
            audio_level_db=-20.0,
            recorded_at=now,
            is_final=True,
        )
    )

    assert len(feedback.signals) == 1
    assert feedback.signals[0].kind == "defer"
    assert feedback.signals[0].scope.topic == "laundry"


@pytest.mark.unit
def test_phase106_feedback_classifier_accepts_and_rejects() -> None:
    scope = CandidateFeedbackScope(source="time_based", topic="break")
    transcript = Transcript(
        text="うん、なに？",
        device_id="desk",
        speaker="user",
        audio_level_db=-20.0,
        recorded_at=datetime(2026, 5, 25, 12, 0, tzinfo=UTC),
        is_final=True,
    )

    signal = classify_feedback(transcript, scope)

    assert signal is not None
    assert signal.kind == "acceptance"


@pytest.mark.unit
async def test_phase106_llm_judge_runner_uses_router_and_json_result() -> None:
    backend = FakeBackend(
        (
            '{"decision":"speak_now","confidence":0.72,',
            '"reason":"short candidate fits","tone":"soft","max_length":"short"}',
        )
    )
    judge = InitiativeLLMJudge(FakeRouter(backend))  # type: ignore[arg-type]

    decision = await judge.judge(
        candidate_text="ねえ、少し休憩しない？",
        candidate_reason="長く作業している",
        policy_decision=CandidateSpeakDecision(
            decision="needs_llm_judge",
            score=0.5,
            threshold=0.68,
            reason="boundary",
        ),
        desire=TomokoDesireState(desire_1m=0.7),
        speakability=SpeakabilityState(presence_1m=1.0),
    )

    assert decision.decision == "speak"
    assert backend.requests
    assert "Return JSON only" in backend.requests[0][0]
