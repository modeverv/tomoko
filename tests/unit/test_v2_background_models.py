from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from server.evaluation import EvaluationLogger
from server.floor_holding import HoldingAction, HoldingStateMachine
from server.follow_up import FollowUpQueue
from server.info.main import calendar_dto_map, parse_minimal_ics, should_candidate_from_world
from server.initiative import InitiativeInputs, InitiativeMotivationModel
from server.lifecycle import PromptLifecycleManager
from server.shared.models import (
    CancelPolicy,
    EvalScore,
    EvalTurn,
    PromptRequest,
    PromptScope,
)
from server.short_reaction import ShortReactionKind, ShortReactionLifecycle, parse_short_reaction
from server.stop import ObedienceArbitrator, classify_stop_intent
from server.think.main import CandidateStore, calendar_reminder_seed
from server.user_status.main import ArtifactRetention, OSMetadata, build_user_status_observation

pytestmark = pytest.mark.unit


def test_short_reaction_format_and_stale_discard() -> None:
    proposal = parse_short_reaction(
        "EMOTION:neutral\n了解",
        ShortReactionKind.SHORT_CONFIRMATION,
    )
    assert proposal.text == "了解"
    with pytest.raises(ValueError):
        parse_short_reaction("了解", ShortReactionKind.LIGHT_ACK)
    request = PromptRequest(
        prompt_text="short",
        scope=PromptScope.SHORT,
        decision_id=None,
        utterance_id=None,
        candidate_id=None,
        priority=100,
        cancel_policy=CancelPolicy.CANCEL_ON_FINAL_DIVERGENCE,
    )
    lifecycle = ShortReactionLifecycle()
    lifecycle.start(request)
    assert lifecycle.discard_if_stale(final_text="ぜんぜん違う", partial_text="たぶん")


def test_initiative_pressure_ema_can_fire_marker() -> None:
    model = InitiativeMotivationModel(alpha=1.0, threshold=0.5)
    would_fire, scores = model.update(
        InitiativeInputs(
            silence_sec=15,
            candidate_pressure=0.9,
            user_present=True,
            p_yielding=0.9,
            intrusion=0.0,
            rejection=0.0,
        )
    )
    assert would_fire
    assert scores["speakability"] >= 0.5


def test_user_status_uses_ocr_and_os_metadata_not_raw_image(tmp_path: Path) -> None:
    artifact = tmp_path / "screen.png"
    artifact.write_bytes(b"png")
    observation = build_user_status_observation(
        present=True,
        ocr_text="pytest failed in Codex terminal",
        metadata=OSMetadata(app_name="Codex", window_title="pytest", url=None),
        artifact_path=str(artifact),
    )
    assert observation.activity_label == "coding_or_terminal"
    assert observation.artifact_path == str(artifact)
    retention = ArtifactRetention(tmp_path, retention_sec=-1)
    assert retention.prune() == [artifact]


def test_info_process_calendar_and_world_filter() -> None:
    events = parse_minimal_ics(
        """BEGIN:VEVENT
DTSTART:20260618T120000
SUMMARY:Design review
END:VEVENT
"""
    )
    assert calendar_dto_map(events) == {"20260618T120000": "Design review"}
    assert should_candidate_from_world(
        confidence=0.9,
        stale=False,
        sensitive=False,
        private=False,
        do_not_speak=False,
    )
    assert not should_candidate_from_world(
        confidence=0.9,
        stale=False,
        sensitive=True,
        private=False,
        do_not_speak=False,
    )


def test_candidate_store_dedupes_and_preserves_lifecycle() -> None:
    store = CandidateStore()
    seed = calendar_reminder_seed("20260618T120000", "Design review")
    first = store.upsert_seed(seed)
    second = store.upsert_seed(seed)
    assert first.id == second.id
    assert len(store.active()) == 1
    assert first.candidate_score > 0


def test_prompt_lifecycle_cancels_by_policy_and_is_idempotent() -> None:
    request = PromptRequest(
        prompt_text="main",
        scope=PromptScope.MAIN,
        decision_id=None,
        utterance_id=None,
        candidate_id=None,
        priority=1,
        cancel_policy=CancelPolicy.CANCEL_ON_USER_SPEAKING,
    )
    manager = PromptLifecycleManager()
    manager.add(request)
    assert manager.cancel_for_user_speaking() == [request.id]
    assert manager.cancel_for_user_speaking() == []
    provisional = PromptRequest(
        prompt_text="p",
        scope=PromptScope.PROVISIONAL,
        decision_id=None,
        utterance_id=None,
        candidate_id=None,
        priority=1,
        cancel_policy=CancelPolicy.CANCEL_ON_FINAL_DIVERGENCE,
    )
    manager.add(provisional)
    assert manager.cancel_for_final_divergence(
        provisional.id,
        provisional="今日は",
        final="明日は",
    )


def test_holding_state_machine_yields_caps_and_continues() -> None:
    machine = HoldingStateMachine(max_count=1)
    action, score = machine.decide(
        pause_ms=800,
        desire=0.9,
        floor_available=0.9,
        fatigue=0.0,
        stop_pressure=0.0,
        user_speaking=False,
    )
    assert action == HoldingAction.CONTINUE
    assert score > 0.55
    assert machine.decide(
        pause_ms=800,
        desire=0.9,
        floor_available=0.9,
        fatigue=0.0,
        stop_pressure=0.0,
        user_speaking=False,
    )[0] == HoldingAction.CAP
    assert machine.decide(
        pause_ms=800,
        desire=0.9,
        floor_available=0.9,
        fatigue=0.0,
        stop_pressure=0.0,
        user_speaking=True,
    )[0] == HoldingAction.YIELD


def test_follow_up_queue_discards_on_user_reaction() -> None:
    queue = FollowUpQueue()
    queue.start_generation(["それでね"], "candidate")
    assert queue.pop_ready() is not None
    queue.start_generation(["続き"], "context")
    queue.discard_on_user_reaction()
    assert queue.pop_ready() is None


def test_stop_arbitration_obeys_second_stop_and_ui_stop() -> None:
    strength = classify_stop_intent("黙って")
    assert strength is not None
    arbitrator = ObedienceArbitrator()
    first, first_scores = arbitrator.arbitrate(strength, desire_score=0.99)
    second, second_scores = arbitrator.arbitrate(strength, desire_score=0.99)
    assert first_scores["compliance_pressure"] > 0
    assert second.value == "obey"
    assert second_scores["obey_score"] >= first_scores["obey_score"]
    assert classify_stop_intent("", explicit_ui_stop=True).value == "system"


def test_evaluation_logger_joins_machine_turn_and_human_score(tmp_path: Path) -> None:
    path = tmp_path / "eval.jsonl"
    session_id = uuid4()
    logger = EvaluationLogger(path)
    turn = EvalTurn(
        session_id=session_id,
        speech_end_to_first_text_ms=100,
        speech_end_to_first_audio_ms=200,
        turn_total_latency_ms=500,
        metrics={"vad_ms": 10},
    )
    score = EvalScore(
        eval_turn_id=turn.id,
        responsiveness=0.8,
        attended_feeling=0.7,
        turn_taking_naturalness=0.9,
        interruption_robustness=0.6,
        memory_naturalness=0.5,
        persona_consistency=0.8,
        recovery_quality=0.7,
    )
    logger.append_turn(turn)
    logger.append_score(score)
    report = logger.joined_report(session_id)
    assert report["turns"][0]["score"]["responsiveness"] == 0.8
