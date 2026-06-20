from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from server.shared.logging import JsonlLogger
from server.shared.models import (
    CancelPolicy,
    DialogueTurnPressure,
    DurableUtterance,
    LlmFireDecision,
    LlmFireGateInput,
    MotivationPressure,
    NaturalSpeechPressure,
    PreparedSpeechCandidate,
    PromptRequest,
    PromptScope,
    SpeechEmissionDecision,
    SpeechEmissionGateInput,
    SpeechOrder,
    SpeechOrderMode,
    SpeechPressureState,
    SpeechSchedulerInput,
    SpeechSchedulerOutput,
    SpeechSchedulerThresholds,
    TurnMaterials,
    WorldPressure,
)
from server.tomoko.db_bridge import (
    close_conversation_session_sql,
    insert_audio_output_event_sql,
    insert_conversation_session_sql,
    insert_prompt_request_for_order_sql,
    insert_prompt_request_sql,
    insert_scheduler_decision_sql,
    insert_speech_order_sql,
    insert_utterance_sql,
    notify_speech_order_sql,
    update_conversation_session_activity_sql,
)
from server.tomoko.gates import LlmFireGate, SpeechEmissionGate
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
