from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from server.shared.logging import JsonlLogger
from server.shared.models import (
    CancelPolicy,
    CandidateLifecycle,
    CandidateRecord,
    ConversationHistoryItem,
    DialogueTurnPressure,
    DurableUtterance,
    LlmFireDecision,
    LlmFireGateInput,
    MotivationPressure,
    NaturalSpeechPressure,
    PersonalityMaterials,
    PreparedSpeechCandidate,
    PromptRequest,
    PromptScope,
    SessionSummary,
    SpeechEmissionDecision,
    SpeechEmissionGateInput,
    SpeechOrder,
    SpeechOrderMode,
    SpeechPressureState,
    SpeechSchedulerInput,
    SpeechSchedulerOutput,
    SpeechSchedulerThresholds,
    TurnMaterials,
    WorldMaterials,
    WorldPressure,
)
from server.tomoko.db_bridge import (
    candidate_from_row,
    close_conversation_session_sql,
    insert_audio_output_event_sql,
    insert_candidate_sql,
    insert_conversation_session_sql,
    insert_prompt_request_for_order_sql,
    insert_prompt_request_sql,
    insert_scheduler_decision_sql,
    insert_session_summary_sql,
    insert_speech_order_sql,
    insert_summary_embedding_sql,
    insert_utterance_sql,
    load_active_candidates,
    notify_speech_order_sql,
    select_active_candidates_sql,
    update_conversation_session_activity_sql,
)
from server.tomoko.gates import LlmFireGate, SpeechEmissionGate
from server.tomoko.pressures import (
    DialogueTurnPressureModel,
    MotivationPressureModel,
    WorldPressureModel,
)
from server.tomoko.scheduler import SpeechScheduler, detect_stop_intent
from server.tomoko.semantic import (
    DEFAULT_DISTILLED_SATURATION_MODEL_PATH,
    DistilledSaturationBackend,
    OpenAICompatibleSaturationBackend,
    SemanticSaturationJudge,
    create_default_saturation_judge,
    deterministic_saturation,
    parse_saturation_output,
    saturation_prompt,
    stable_prefix,
)

pytestmark = pytest.mark.unit


def test_parse_saturation_output_accepts_only_single_fixed_line() -> None:
    assert parse_saturation_output("SATURATION=0.72").saturation == 0.72
    assert parse_saturation_output("  SATURATION=1.0  ").saturation == 1.0

    for output in [
        "",
        "SATURATION=1.2",
        "SATURATION=-0.1",
        "REASON=done\nSATURATION=0.8",
        "SATURATION=high",
    ]:
        with pytest.raises(ValueError):
            parse_saturation_output(output)


def test_openai_saturation_backend_builds_small_non_stream_payload() -> None:
    backend = OpenAICompatibleSaturationBackend(
        url="http://127.0.0.1:8083",
        model="mlx-community/gemma-4-e2b-it-OptiQ-4bit",
    )

    payload = backend.payload("TEXT=トモコ、予定を教えて")

    assert payload["model"] == "mlx-community/gemma-4-e2b-it-OptiQ-4bit"
    assert payload["stream"] is False
    assert payload["max_tokens"] == 16
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert payload["messages"] == [
        {
            "role": "system",
            "content": (
                "あなたは会話発話可能判定器です。"
                "必ず SATURATION=<0.0から1.0の数値> の1行だけを返してください。"
            ),
        },
        {"role": "user", "content": "TEXT=トモコ、予定を教えて"},
    ]


def test_saturation_prompt_uses_conversation_readiness_examples_for_e2b() -> None:
    prompt = saturation_prompt("こんにちは聞こえますか")

    assert "会話相手が今返し始めてよい度合い" in prompt
    assert "TEXT=えっと\nSATURATION=0.10" in prompt
    assert "TEXT=今日の予定を教えて\nSATURATION=0.95" in prompt
    assert "TEXT=今の返事ちゃんと聞こえてる\nSATURATION=0.85" in prompt
    assert "Tomoko" not in prompt
    assert "トモコ" not in prompt
    assert prompt.endswith("TEXT=こんにちは聞こえますか")


def test_deterministic_saturation_fallback_handles_representative_cases() -> None:
    assert deterministic_saturation("").saturation == 0.0
    assert deterministic_saturation("え").saturation < 0.25
    assert deterministic_saturation("トモコ、予定を教えて").saturation >= 0.75
    assert deterministic_saturation("これでいい?").saturation >= 0.75
    assert deterministic_saturation("ただ、やっぱり").saturation < 0.45
    assert stable_prefix(["トモコ、今日の予定", "トモコ、今日の予定を"]) == "トモコ、今日の予定"


def test_distilled_saturation_scores_partials_as_final_and_clamps_short_acks() -> None:
    class FakeModel:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bool]] = []

        def predict(self, text: str, *, is_final: bool = False) -> float:
            self.calls.append((text, is_final))
            return 0.92

    model = FakeModel()
    backend = DistilledSaturationBackend(model=model)

    partial = backend.judge_sync("今日の予定を教えて", partial=True)
    short_final = backend.judge_sync("はい", partial=False)

    assert model.calls[0] == ("今日の予定を教えて", True)
    assert partial.source == "distilled_partial_finalish"
    assert partial.saturation == pytest.approx(0.92)
    assert short_final.source == "distilled_short_ack_rule"
    assert short_final.saturation == pytest.approx(0.35)


def test_default_distilled_saturation_model_points_to_existing_public_artifact() -> None:
    assert DEFAULT_DISTILLED_SATURATION_MODEL_PATH.name == (
        "public-synthetic-gemma26b-200-plus-anchors-life-h8192-l001-saturation-model.json"
    )
    assert DEFAULT_DISTILLED_SATURATION_MODEL_PATH.exists()

    makefile = Path("Makefile").read_text(encoding="utf-8")
    assert (
        "TOMOKO_V2_DISTILLED_SATURATION_MODEL ?= "
        "make-model/artifacts/"
        "public-synthetic-gemma26b-200-plus-anchors-life-h8192-l001-saturation-model.json"
    ) in makefile


def test_default_saturation_judge_falls_back_when_artifact_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "TOMOKO_V2_DISTILLED_SATURATION_MODEL",
        "make-model/artifacts/missing-saturation-model.json",
    )

    judge = create_default_saturation_judge()

    assert judge.distilled_backend is None


@pytest.mark.asyncio
async def test_semantic_saturation_judge_falls_back_and_logs(tmp_path: Path) -> None:
    class BrokenBackend:
        async def complete(self, prompt: str) -> str:
            assert "トモコ" in prompt
            return "not fixed line"

    log_path = tmp_path / "semantic.jsonl"
    judge = SemanticSaturationJudge(llm_backend=BrokenBackend(), logger=JsonlLogger(log_path))
    result = await judge.judge("トモコ、予定を教えて")

    assert result.saturation >= 0.75
    assert result.source == "deterministic_fallback"
    assert "semantic_saturation" in log_path.read_text(encoding="utf-8")


def test_speech_scheduler_user_reply_replace_current_and_breakdown() -> None:
    output = SpeechScheduler().decide(
        SpeechSchedulerInput(
            final_stt_text="トモコ、短く返事して",
            semantic_saturation=0.9,
            silence_ms=600,
            p_yielding=0.95,
        )
    )

    assert output.action == "replace_current"
    assert output.text_intent == "reply"
    assert output.score_breakdown["saturation"] > 0
    assert output.llm_prompt_basis


def test_llm_fire_gate_synthesizes_dialogue_pressure_for_fire() -> None:
    materials = TurnMaterials(
        window_ms=200,
        user_speaking=True,
        speech_probability=0.74,
        p_yielding=0.94,
        silence_ms=120,
        playback_active=False,
        stt_partial="今日の予定を",
    )
    decision = LlmFireGate().decide(
        LlmFireGateInput(
            turn_materials=materials,
            dialogue_pressure=DialogueTurnPressure(
                reply_readiness=0.86,
                turn_opportunity=0.94,
                interruption_risk=0.04,
                semantic_saturation=0.78,
                text_presence=1.0,
            ),
            motivation_pressure=MotivationPressure(initiative_desire=0.2),
        )
    )

    assert decision.decision == LlmFireDecision.FIRE
    assert decision.score_breakdown["dialogue_reply_readiness"] > 0
    assert "pressure synthesis" in decision.reason


def test_dialogue_turn_pressure_tracks_vap_yielding_separately_from_silence() -> None:
    pressure = DialogueTurnPressureModel().calculate(
        turn_materials=TurnMaterials(
            window_ms=200,
            user_speaking=False,
            speech_probability=0.0,
            p_yielding=0.92,
            silence_ms=120,
            playback_active=False,
            stt_partial="今日の予定",
        ),
        semantic_saturation=0.5,
        stable_prefix="今日の予定",
    )

    assert pressure.yielding_opportunity == pytest.approx(0.92)
    assert pressure.silence_opportunity == pytest.approx(0.1)
    assert pressure.turn_opportunity == pytest.approx(0.92)


def test_dialogue_turn_pressure_keeps_silence_fallback_visible() -> None:
    pressure = DialogueTurnPressureModel().calculate(
        turn_materials=TurnMaterials(
            window_ms=200,
            user_speaking=False,
            speech_probability=0.0,
            p_yielding=None,
            silence_ms=900,
            playback_active=False,
            stt_partial="今日の予定",
        ),
        semantic_saturation=0.5,
        stable_prefix="今日の予定",
    )

    assert pressure.yielding_opportunity == pytest.approx(0.0)
    assert pressure.silence_opportunity == pytest.approx(0.75)
    assert pressure.turn_opportunity == pytest.approx(0.75)


def test_llm_fire_gate_synthesizes_motivation_pressure_without_stt() -> None:
    materials = TurnMaterials(
        window_ms=200,
        user_speaking=False,
        speech_probability=0.0,
        p_yielding=1.0,
        silence_ms=9000,
        playback_active=False,
    )
    decision = LlmFireGate().decide(
        LlmFireGateInput(
            turn_materials=materials,
            dialogue_pressure=DialogueTurnPressure(turn_opportunity=1.0),
            motivation_pressure=MotivationPressure(
                initiative_desire=0.9,
                personality_push=0.8,
            ),
        )
    )

    assert decision.decision == LlmFireDecision.FIRE
    assert decision.score >= 0.55


def test_motivation_pressure_model_turns_heat_and_topic_into_threshold_shift() -> None:
    model = MotivationPressureModel()
    turn = TurnMaterials(
        window_ms=200,
        user_speaking=True,
        speech_probability=0.25,
        p_yielding=0.8,
        silence_ms=120,
        playback_active=False,
        stt_partial="その予定の話なんだけど",
    )
    low = model.calculate(
        turn_materials=turn,
        personality_materials=PersonalityMaterials(
            talkativeness=0.2,
            curiosity=0.2,
            restraint=0.8,
            interrupt_tolerance=0.1,
        ),
        recent_history=[],
        current_text="その予定の話なんだけど",
    )
    high = model.calculate(
        turn_materials=turn,
        personality_materials=PersonalityMaterials(
            talkativeness=0.9,
            curiosity=0.9,
            restraint=0.1,
            interrupt_tolerance=0.8,
        ),
        recent_history=[
            ConversationHistoryItem(speaker="user", text="今日の予定の話をしよう"),
            ConversationHistoryItem(speaker="tomoko", text="予定、気になってる"),
            ConversationHistoryItem(speaker="user", text="その予定なんだけど"),
            ConversationHistoryItem(speaker="tomoko", text="続き聞きたい"),
        ],
        current_text="その予定の話なんだけど",
    )

    assert high.conversation_heat > low.conversation_heat
    assert high.topic_continuity > low.topic_continuity
    assert high.threshold_shift > low.threshold_shift
    assert high.threshold_shift > 0.1


def test_world_pressure_model_suppresses_speaking_when_user_is_absent() -> None:
    model = WorldPressureModel()
    turn = TurnMaterials(
        window_ms=200,
        user_speaking=False,
        speech_probability=0.0,
        p_yielding=0.9,
        silence_ms=3000,
        playback_active=False,
    )
    present = model.calculate(
        turn_materials=turn,
        world_materials=WorldMaterials(
            calendar_urgency=0.9,
            external_result_importance=0.7,
            user_present=True,
        ),
        personality_materials=PersonalityMaterials(restraint=0.1),
    )
    absent = model.calculate(
        turn_materials=turn,
        world_materials=WorldMaterials(
            calendar_urgency=0.9,
            external_result_importance=0.7,
            user_present=False,
        ),
        personality_materials=PersonalityMaterials(restraint=0.1),
    )

    assert present.urgency > 0.0
    assert present.deliverability > 0.0
    assert present.user_presence == pytest.approx(1.0)
    assert absent.importance == pytest.approx(0.0)
    assert absent.urgency == pytest.approx(0.0)
    assert absent.deliverability == pytest.approx(0.0)
    assert absent.user_presence == pytest.approx(0.0)
    assert absent.user_absence == pytest.approx(1.0)


def test_world_pressure_model_includes_candidate_pressure() -> None:
    model = WorldPressureModel()
    turn = TurnMaterials(
        window_ms=200,
        user_speaking=False,
        speech_probability=0.0,
        p_yielding=0.8,
        silence_ms=2400,
        playback_active=False,
    )

    pressure = model.calculate(
        turn_materials=turn,
        world_materials=WorldMaterials(candidate_pressure=0.85),
        personality_materials=PersonalityMaterials(curiosity=0.7, restraint=0.1),
    )

    assert pressure.importance >= 0.85
    assert pressure.relevance >= 0.85
    assert pressure.candidate_pressure == pytest.approx(0.85)


def test_llm_fire_gate_lowers_fire_threshold_when_motivation_is_high() -> None:
    materials = TurnMaterials(
        window_ms=200,
        user_speaking=False,
        speech_probability=0.0,
        p_yielding=0.4,
        silence_ms=200,
        playback_active=False,
        stt_partial="それは違う気が",
    )
    base_input = LlmFireGateInput(
        turn_materials=materials,
        dialogue_pressure=DialogueTurnPressure(
            reply_readiness=0.56,
            turn_opportunity=0.25,
            semantic_saturation=0.55,
            text_presence=1.0,
        ),
        motivation_pressure=MotivationPressure(
            initiative_desire=0.25,
            threshold_shift=0.0,
        ),
    )
    low = LlmFireGate().decide(base_input)
    high = LlmFireGate().decide(
        LlmFireGateInput(
            turn_materials=materials,
            dialogue_pressure=base_input.dialogue_pressure,
            motivation_pressure=MotivationPressure(
                initiative_desire=0.25,
                threshold_shift=0.14,
            ),
        )
    )

    assert low.decision == LlmFireDecision.DO_NOT_FIRE
    assert high.decision == LlmFireDecision.FIRE
    assert high.score_breakdown["motivation_threshold_shift"] == pytest.approx(0.14)
    assert "motivation threshold shift" in high.reason


def test_speech_emission_gate_uses_materials_and_pressure_for_barge_in_risk() -> None:
    current = SpeechOrder(
        text="今の返事",
        mode=SpeechOrderMode.REPLACE_CURRENT,
        reason="current",
        priority=50,
    )
    gate = SpeechEmissionGate()

    hold = gate.decide(
        SpeechEmissionGateInput(
            candidate=PreparedSpeechCandidate(
                text="割り込む候補",
                priority=0.8,
                freshness=1.0,
                semantic_confidence=0.5,
            ),
            turn_materials=TurnMaterials(
                window_ms=200,
                user_speaking=True,
                speech_probability=0.95,
                p_yielding=0.2,
                silence_ms=0,
                playback_active=True,
            ),
            dialogue_pressure=DialogueTurnPressure(
                turn_opportunity=0.1,
                interruption_risk=0.76,
            ),
            motivation_pressure=MotivationPressure(initiative_desire=0.4),
            current_speech_order=current,
            current_speech_score=0.7,
        )
    )
    emit = gate.decide(
        SpeechEmissionGateInput(
            candidate=PreparedSpeechCandidate(
                text="出してよい候補",
                priority=0.9,
                freshness=1.0,
                semantic_confidence=0.68,
            ),
            turn_materials=TurnMaterials(
                window_ms=200,
                user_speaking=True,
                speech_probability=0.45,
                p_yielding=0.92,
                silence_ms=160,
                playback_active=False,
            ),
            dialogue_pressure=DialogueTurnPressure(
                turn_opportunity=0.92,
                interruption_risk=0.04,
            ),
            natural_speech_pressure=NaturalSpeechPressure(naturalness=0.7),
            motivation_pressure=MotivationPressure(initiative_desire=0.9),
            world_pressure=WorldPressure(deliverability=0.5),
            current_speech_order=current,
            current_speech_score=0.2,
        )
    )

    assert hold.decision == SpeechEmissionDecision.HOLD
    assert hold.score_breakdown["interruption_risk"] < 0
    assert emit.decision == SpeechEmissionDecision.REPLACE_CURRENT
    assert emit.score_breakdown["motivation"] > 0


def test_speech_emission_gate_lowers_emit_threshold_when_motivation_is_high() -> None:
    materials = TurnMaterials(
        window_ms=200,
        user_speaking=False,
        speech_probability=0.0,
        p_yielding=0.5,
        silence_ms=200,
        playback_active=False,
    )
    candidate = PreparedSpeechCandidate(
        text="いや、それってさ。",
        priority=0.6,
        freshness=0.5,
        semantic_confidence=0.4,
    )
    base = SpeechEmissionGateInput(
        candidate=candidate,
        turn_materials=materials,
        dialogue_pressure=DialogueTurnPressure(turn_opportunity=0.1),
        motivation_pressure=MotivationPressure(
            initiative_desire=0.2,
            threshold_shift=0.0,
        ),
    )
    low = SpeechEmissionGate().decide(base)
    high = SpeechEmissionGate().decide(
        SpeechEmissionGateInput(
            candidate=candidate,
            turn_materials=materials,
            dialogue_pressure=base.dialogue_pressure,
            motivation_pressure=MotivationPressure(
                initiative_desire=0.2,
                threshold_shift=0.14,
            ),
        )
    )

    assert low.decision == SpeechEmissionDecision.SUPPRESS
    assert high.decision == SpeechEmissionDecision.EMIT_NOW
    assert high.score_breakdown["motivation_threshold_shift"] == pytest.approx(0.14)
    assert "motivation threshold shift" in high.reason


def test_speech_scheduler_suppresses_low_saturation_partial_start() -> None:
    output = SpeechScheduler().decide(
        SpeechSchedulerInput(
            partial_stt_text="トモコ",
            stable_prefix="トモコ",
            semantic_saturation=0.3,
            p_yielding=0.95,
        )
    )

    assert output.action == "suppress"
    assert "partial semantic saturation" in output.reason


def test_speech_scheduler_allows_partial_when_score_is_high_enough() -> None:
    output = SpeechScheduler().decide(
        SpeechSchedulerInput(
            partial_stt_text="こんにちは今の気分を教えて下さい",
            stable_prefix="こんにちは今の気分を教えて下さい",
            semantic_saturation=0.5,
        )
    )

    assert output.action == "replace_current"
    assert output.reason == "reply pressure crossed threshold"
    assert output.score == pytest.approx(0.775)


def test_speech_scheduler_appends_calendar_while_speaking() -> None:
    current = SpeechOrder(
        text="先に返事しているよ",
        mode=SpeechOrderMode.REPLACE_CURRENT,
        reason="current",
        priority=50,
    )
    output = SpeechScheduler().decide(
        SpeechSchedulerInput(
            tomoko_currently_speaking=True,
            current_speech_order=current,
            current_speech_score=0.8,
            calendar_urgency=1.0,
            semantic_saturation=0.0,
            silence_ms=0,
        )
    )

    assert output.action == "append_after_current"
    assert output.text_intent == "calendar_notice"
    assert "calendar" in output.reason


def test_speech_scheduler_stop_and_interruption_suppression() -> None:
    scheduler = SpeechScheduler()
    assert detect_stop_intent("トモコ、止めて") == 1.0
    stop = scheduler.decide(SpeechSchedulerInput(final_stt_text="止めて", stop_intent=1.0))
    assert stop.action == "stop"
    assert stop.text_intent == "stop"

    suppress = scheduler.decide(
        SpeechSchedulerInput(
            user_speaking=True,
            tomoko_currently_speaking=True,
            semantic_saturation=0.2,
            pressure_state=SpeechPressureState(interruption_penalty=1.0),
        )
    )
    assert suppress.action == "suppress"
    assert suppress.score < 0


def test_speech_scheduler_replaces_when_new_score_beats_current_margin() -> None:
    current = SpeechOrder(
        text="古い返答",
        mode=SpeechOrderMode.REPLACE_CURRENT,
        reason="old",
        priority=40,
    )
    output = SpeechScheduler(
        thresholds=SpeechSchedulerThresholds(replace_margin=0.2)
    ).decide(
        SpeechSchedulerInput(
            current_speech_order=current,
            current_speech_score=0.4,
            semantic_saturation=1.0,
            p_yielding=1.0,
            final_stt_text="いや、別の質問",
        )
    )

    assert output.action == "replace_current"
    assert output.score > 0.6


def test_scheduler_output_logs_structured_decision(tmp_path: Path) -> None:
    log_path = tmp_path / "runtime.jsonl"
    scheduler = SpeechScheduler(logger=JsonlLogger(log_path))
    output: SpeechSchedulerOutput = scheduler.decide(
        SpeechSchedulerInput(final_stt_text="これでいい?", semantic_saturation=0.8)
    )

    assert output.score_breakdown
    payload = log_path.read_text(encoding="utf-8")
    assert "speech_scheduler_decision" in payload
    assert "score_breakdown" in payload


def test_speech_order_db_bridge_uses_row_body_and_id_only_notify() -> None:
    output = SpeechScheduler().decide(
        SpeechSchedulerInput(final_stt_text="これでいい?", semantic_saturation=0.8)
    )
    order = SpeechOrder(
        text="いいと思うよ。",
        mode=SpeechOrderMode.REPLACE_CURRENT,
        reason=output.reason,
        priority=80,
        scheduler_decision_id=output.id,
    )

    decision_sql = insert_scheduler_decision_sql(
        output,
        stt_observation_id=None,
        semantic_saturation_id=None,
    )
    order_sql = insert_speech_order_sql(order)
    notify_query, notify_params = notify_speech_order_sql(order.id)
    prompt_sql = insert_prompt_request_for_order_sql(order)

    assert "v2_speech_scheduler_decisions" in decision_sql.query
    assert "v2_speech_orders" in order_sql.query
    assert "v2_prompt_requests" in prompt_sql.query
    assert "context_snapshot_id" not in prompt_sql.query
    assert "utterance_id" not in prompt_sql.query
    assert "candidate_id" not in prompt_sql.query
    assert "SELECT pg_notify" in notify_query
    assert notify_params["payload"] == str(order.id)

    chunk = __import__("server.shared.models").shared.models.AudioChunkOut(
        request_id=order.id,
        chunk=b"RIFFxxxxWAVEdata",
        sample_rate=16000,
        is_final=True,
        trace_id=order.trace_id,
    )
    audio_sql = insert_audio_output_event_sql(chunk)
    assert "v2_audio_output_events" in audio_sql.query
    assert len(chunk.chunk) in audio_sql.params


def test_prompt_request_sql_does_not_reference_unpersisted_snapshot_fk() -> None:
    request = PromptRequest(
        prompt_text="返事して",
        scope=PromptScope.MAIN,
        decision_id=None,
        utterance_id=uuid4(),
        candidate_id=uuid4(),
        priority=50,
        cancel_policy=CancelPolicy.KEEP_UNTIL_COMPLETE,
        context_snapshot_id=uuid4(),
    )

    sql = insert_prompt_request_sql(request)

    assert "v2_prompt_requests" in sql.query
    assert "context_snapshot_id" not in sql.query
    assert "utterance_id" not in sql.query
    assert "candidate_id" not in sql.query
    assert request.context_snapshot_id not in sql.params
    assert request.utterance_id not in sql.params
    assert request.candidate_id not in sql.params


def test_session_summary_db_bridge_writes_summary_and_embedding() -> None:
    session_id = uuid4()
    summary = SessionSummary(
        session_id=session_id,
        keyword="予定",
        conclusion="予定の相談をしていた",
        embedding=(0.1, 0.2, 0.3),
    )

    summary_sql = insert_session_summary_sql(summary)
    embedding_sql = insert_summary_embedding_sql(summary)

    assert "v2_session_summaries" in summary_sql.query
    assert "summary_text" in summary_sql.query
    assert "予定: 予定の相談をしていた" in summary_sql.params
    assert "v2_summary_embeddings" in embedding_sql.query
    assert summary.id in embedding_sql.params
    assert [0.1, 0.2, 0.3] in embedding_sql.params


def test_candidate_db_bridge_upserts_and_round_trips_row() -> None:
    record = CandidateRecord(
        seed_id=uuid4(),
        source="world",
        source_key="rain-now",
        text="いま外は雨が降っている",
        priority=0.8,
        urgency=0.6,
        intrusion=0.1,
        maturity=1.0,
        lifecycle=CandidateLifecycle.ACTIVE,
        context_tags=("weather", "world"),
        candidate_score=0.9,
    )

    sql = insert_candidate_sql(record)

    assert "v2_candidates" in sql.query
    assert "ON CONFLICT (source, source_key) DO UPDATE" in sql.query
    assert record.id in sql.params
    assert record.seed_id in sql.params
    assert ["weather", "world"] in sql.params

    restored = candidate_from_row(
        {
            "id": record.id,
            "seed_id": record.seed_id,
            "source": record.source,
            "source_key": record.source_key,
            "text": record.text,
            "priority": record.priority,
            "urgency": record.urgency,
            "intrusion": record.intrusion,
            "maturity": record.maturity,
            "candidate_score": record.candidate_score,
            "lifecycle": record.lifecycle.value,
            "context_tags": list(record.context_tags),
            "expires_at": record.expires_at,
            "spoken_at": record.spoken_at,
            "trace_id": record.trace_id,
            "created_at": record.created_at,
        }
    )

    assert restored == record


class _CandidateRowsCursor:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    async def fetchall(self) -> list[dict[str, object]]:
        return self.rows


class _CandidateRowsConnection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(
        self,
        query: str,
        params: tuple[object, ...],
    ) -> _CandidateRowsCursor:
        self.calls.append((query, params))
        return _CandidateRowsCursor(self.rows)


@pytest.mark.asyncio
async def test_active_candidate_db_bridge_reads_ordered_rows() -> None:
    record = CandidateRecord(
        seed_id=uuid4(),
        source="world",
        source_key="rain-now",
        text="いま外は雨が降っている",
        priority=0.8,
        urgency=0.6,
        intrusion=0.1,
        maturity=1.0,
        lifecycle=CandidateLifecycle.ACTIVE,
        context_tags=("weather", "world"),
        candidate_score=0.9,
    )
    command = select_active_candidates_sql(limit=3)
    conn = _CandidateRowsConnection(
        [
            {
                "id": record.id,
                "seed_id": record.seed_id,
                "source": record.source,
                "source_key": record.source_key,
                "text": record.text,
                "priority": record.priority,
                "urgency": record.urgency,
                "intrusion": record.intrusion,
                "maturity": record.maturity,
                "candidate_score": record.candidate_score,
                "lifecycle": record.lifecycle.value,
                "context_tags": list(record.context_tags),
                "expires_at": record.expires_at,
                "spoken_at": record.spoken_at,
                "trace_id": record.trace_id,
                "created_at": record.created_at,
            }
        ]
    )

    loaded = await load_active_candidates(conn, limit=3)

    assert "FROM v2_candidates" in command.query
    assert "lifecycle = %s" in command.query
    assert "expires_at IS NULL OR expires_at > now()" in command.query
    assert "ORDER BY candidate_score DESC" in command.query
    assert command.params == (CandidateLifecycle.ACTIVE.value, 3)
    assert conn.calls[0] == (command.query, command.params)
    assert loaded == [record]


def test_conversation_session_and_utterance_db_bridge_sql() -> None:
    session_id = uuid4()
    trace_id = uuid4()
    utterance = DurableUtterance(
        session_id=session_id,
        speaker="user",
        text="最初に短く返事して",
        stt_observation_id=uuid4(),
        trace_id=trace_id,
    )

    session_sql = insert_conversation_session_sql(
        session_id=session_id,
        activity_at=utterance.created_at,
        trace_id=trace_id,
    )
    touch_sql = update_conversation_session_activity_sql(
        session_id=session_id,
        activity_at=utterance.created_at,
    )
    close_sql = close_conversation_session_sql(
        session_id=session_id,
        ended_at=utterance.created_at,
        reason="idle_gap",
    )
    utterance_sql = insert_utterance_sql(utterance)

    assert "v2_conversation_sessions" in session_sql.query
    assert "ended_at IS NULL" not in session_sql.query
    assert "last_activity_at" in touch_sql.query
    assert "close_reason" in close_sql.query
    assert "v2_utterances" in utterance_sql.query
    assert utterance.session_id in utterance_sql.params
    assert utterance.text in utterance_sql.params
