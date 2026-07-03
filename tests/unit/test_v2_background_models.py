from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from server.evaluation import EvaluationLogger
from server.floor_holding import HoldingAction, HoldingStateMachine
from server.follow_up import FollowUpQueue
from server.info.main import (
    calendar_dto_map,
    materialize_info_fixtures_from_payloads,
    parse_minimal_ics,
    should_candidate_from_world,
)
from server.initiative import InitiativeInputs, InitiativeMotivationModel
from server.lifecycle import PromptLifecycleManager
from server.shared.models import (
    CancelPolicy,
    EvalScore,
    EvalTurn,
    PromptRequest,
    PromptScope,
    WorldMaterials,
    utc_now,
)
from server.short_reaction import ShortReactionKind, ShortReactionLifecycle, parse_short_reaction
from server.stop import ObedienceArbitrator, classify_stop_intent
from server.summary.main import materialize_summaries_from_db, summarize_session
from server.think.main import (
    CandidateStore,
    build_candidates,
    calendar_item_seeds,
    calendar_reminder_seed,
    materialize_candidates_from_db,
    summary_memory_seed,
    world_info_seed,
    world_seed_from_interpretation_row,
)
from server.user_status.main import (
    ArtifactRetention,
    OSMetadata,
    build_user_status_observation,
    world_materials_from_user_status,
)

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


def test_user_status_presence_maps_to_world_materials() -> None:
    observation = build_user_status_observation(
        present=False,
        ocr_text="",
        metadata=OSMetadata(app_name=None, window_title=None, url=None),
        artifact_path=None,
    )
    base = WorldMaterials(calendar_urgency=0.8, external_result_importance=0.6)

    materials = world_materials_from_user_status(observation, base=base)

    assert materials.user_present is False
    assert materials.user_status_confidence == pytest.approx(0.3)
    assert materials.calendar_urgency == pytest.approx(0.8)
    assert materials.external_result_importance == pytest.approx(0.6)


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


def test_think_process_builds_candidates_from_summary_calendar_and_world() -> None:
    summary = summarize_session(
        uuid4(),
        [
            "予定 明日は雨なら会議前に傘を確認する",
            "忘れないようにしたい",
        ],
    )
    world_seed = world_info_seed(
        source_key="weather-rain",
        text="外は雨が強くなっている",
        confidence=0.9,
        stale=False,
        sensitive=False,
        private=False,
        do_not_speak=False,
    )
    assert calendar_item_seeds({"20260704T120000": "Design review"})[0].source == "calendar"
    assert summary_memory_seed(summary).source == "summary"

    records = build_candidates(
        calendar_items={"20260704T120000": "Design review"},
        summaries=[summary],
        world_seeds=[world_seed],
    )

    assert {record.source for record in records} == {"calendar", "summary", "world"}
    assert {record.source_key for record in records} == {
        "20260704T120000",
        str(summary.id),
        "weather-rain",
    }
    assert all(record.lifecycle == "active" for record in records)
    assert all(record.candidate_score > 0.0 for record in records)
    assert any("雨" in record.text for record in records)


def test_think_process_filters_blocked_world_info() -> None:
    assert world_info_seed(
        source_key="private-tab",
        text="秘密のメモが開かれている",
        confidence=0.95,
        stale=False,
        sensitive=False,
        private=True,
        do_not_speak=False,
    ) is None


class _InfoFixtureConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, params: tuple[object, ...]) -> _ThinkRowsCursor:
        self.calls.append((query, params))
        return _ThinkRowsCursor([])


@pytest.mark.asyncio
async def test_info_process_materializes_fake_fixtures_into_world_rows() -> None:
    conn = _InfoFixtureConnection()

    result = await materialize_info_fixtures_from_payloads(
        conn,
        calendar_items={"20260704T120000": "Design review"},
        world_items=[
            {
                "source_key": "weather-rain-now",
                "text": "外は雨が強くなっている",
                "confidence": 0.9,
                "flags": {"stale": False, "sensitive": False},
            }
        ],
    )

    document_calls = [call for call in conn.calls if "INSERT INTO v2_world_documents" in call[0]]
    item_calls = [call for call in conn.calls if "INSERT INTO v2_world_items" in call[0]]
    interpretation_calls = [
        call for call in conn.calls if "INSERT INTO v2_world_interpretations" in call[0]
    ]
    assert result.documents_upserted == 2
    assert result.items_upserted == 2
    assert result.interpretations_upserted == 2
    assert len(document_calls) == 2
    assert len(item_calls) == 2
    assert len(interpretation_calls) == 2
    assert any("calendar" in call[1] for call in document_calls)
    assert any("world" in call[1] for call in document_calls)
    assert any("Design review" in str(call[1]) for call in interpretation_calls)
    assert any("雨" in str(call[1]) for call in interpretation_calls)


class _ThinkRowsCursor:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    async def fetchall(self) -> list[dict[str, object]]:
        return self.rows


class _ThinkCandidateConnection:
    def __init__(self) -> None:
        now = utc_now()
        self.summary_id = uuid4()
        self.session_id = uuid4()
        self.world_interpretation_id = uuid4()
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.summary_rows = [
            {
                "id": self.summary_id,
                "session_id": self.session_id,
                "keyword": "予定",
                "conclusion": "雨なら会議前に傘を確認する",
                "embedding": [0.1, 0.2, 0.3],
                "trace_id": uuid4(),
                "created_at": now,
            }
        ]
        self.world_rows = [
            {
                "id": self.world_interpretation_id,
                "summary": "外は雨が強くなっている",
                "confidence": 0.9,
                "flags": {"stale": False, "sensitive": False},
                "trace_id": uuid4(),
                "created_at": now,
            }
        ]

    async def execute(
        self,
        query: str,
        params: tuple[object, ...],
    ) -> _ThinkRowsCursor:
        self.calls.append((query, params))
        if "FROM v2_session_summaries" in query:
            return _ThinkRowsCursor(self.summary_rows)
        if "FROM v2_world_interpretations" in query:
            return _ThinkRowsCursor(self.world_rows)
        return _ThinkRowsCursor([])


@pytest.mark.asyncio
async def test_think_process_materializes_db_rows_into_candidates() -> None:
    conn = _ThinkCandidateConnection()
    world_seed = world_seed_from_interpretation_row(conn.world_rows[0])

    result = await materialize_candidates_from_db(
        conn,
        summary_limit=1,
        world_limit=1,
    )

    insert_calls = [call for call in conn.calls if "INSERT INTO v2_candidates" in call[0]]
    assert world_seed is not None
    assert world_seed.source == "world"
    assert world_seed.source_key == str(conn.world_interpretation_id)
    assert result.summaries_read == 1
    assert result.world_items_read == 1
    assert result.candidates_upserted == 2
    assert len(insert_calls) == 2
    assert any(str(conn.summary_id) in str(call[1]) for call in insert_calls)
    assert any(str(conn.world_interpretation_id) in str(call[1]) for call in insert_calls)


class _SummaryCandidateConnection:
    def __init__(self) -> None:
        self.session_id = uuid4()
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.session_rows = [{"id": self.session_id}]
        self.utterance_rows = [
            {"text": "予定 今日は会議が多い"},
            {"text": "優先順位を整理しよう"},
        ]

    async def execute(
        self,
        query: str,
        params: tuple[object, ...],
    ) -> _ThinkRowsCursor:
        self.calls.append((query, params))
        if "FROM v2_conversation_sessions" in query:
            return _ThinkRowsCursor(self.session_rows)
        if "FROM v2_utterances" in query:
            assert params == (self.session_id,)
            return _ThinkRowsCursor(self.utterance_rows)
        return _ThinkRowsCursor([])


@pytest.mark.asyncio
async def test_summary_process_materializes_closed_sessions_into_summary_rows() -> None:
    conn = _SummaryCandidateConnection()

    result = await materialize_summaries_from_db(conn, limit=1)

    summary_calls = [call for call in conn.calls if "INSERT INTO v2_session_summaries" in call[0]]
    embedding_calls = [
        call for call in conn.calls if "INSERT INTO v2_summary_embeddings" in call[0]
    ]
    assert result.sessions_read == 1
    assert result.summaries_upserted == 1
    assert len(summary_calls) == 1
    assert len(embedding_calls) == 1
    assert conn.session_id in summary_calls[0][1]
    assert "予定" in summary_calls[0][1]
    assert "会議" in str(summary_calls[0][1])


def test_summary_process_builds_keyword_conclusion_and_embedding() -> None:
    session_id = uuid4()
    summary = summarize_session(
        session_id,
        [
            "予定 今日は会議が多い",
            "優先順位を整理しよう",
        ],
    )

    assert summary.session_id == session_id
    assert summary.keyword == "予定"
    assert "会議" in summary.conclusion
    assert len(summary.embedding) == 8
    assert sum(summary.embedding) == pytest.approx(1.0)


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
