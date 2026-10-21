from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
import websockets
from fastapi.testclient import TestClient

from server.hot_path import ws_control
from server.hot_path.turn_materials import TurnMaterialAggregator
from server.hot_path.ws_control import RemoteTomokoWsCore, stop_order_from_cancel_event
from server.llm.chat import StaticChatBackend
from server.shared.models import (
    CandidateLifecycle,
    CandidateRecord,
    PartialTranscriptObservation,
    ResponseKind,
    SpeechOrder,
    SpeechOrderMode,
    TurnMaterials,
    UserStatusObservation,
    utc_now,
)
from server.tomoko import realtime as tomoko_realtime
from server.tomoko.conversation import TomokoConversationCore
from server.tomoko.realtime import (
    _fake_personality_materials,
    _fake_user_status_observation,
)
from server.tomoko.realtime import (
    app as tomoko_realtime_app,
)
from server.tomoko.scheduler import SpeechScheduler
from server.tomoko.semantic import SemanticSaturationJudge
from server.tomoko.session import SessionBoundaryModel
from server.tomoko.turn_state import TurnMaterialState

pytestmark = pytest.mark.unit


def test_turn_material_aggregator_builds_200ms_materials() -> None:
    aggregator = TurnMaterialAggregator(window_ms=200)

    assert aggregator.observe_audio((0.1, -0.1), now_ms=0.0) is None
    materials = aggregator.observe_audio((0.2, -0.2), now_ms=200.0)
    aggregator.observe_maai_result({"p_bc_react": 0.62, "p_bc_emo": 0.21, "p_yielding": 0.88})
    materials = aggregator.snapshot(now_ms=400.0, stt_partial="今日の予定を")

    assert materials is not None
    assert materials.window_ms == 200
    assert materials.p_bc_react == pytest.approx(0.62)
    assert materials.p_yielding == pytest.approx(0.88)
    assert materials.stt_partial == "今日の予定を"
    assert materials.speech_probability > 0


def test_turn_material_aggregator_logs_maai_yield_materials(
    capsys: pytest.CaptureFixture[str],
) -> None:
    aggregator = TurnMaterialAggregator(window_ms=200)

    aggregator.observe_maai_result(
        {"p_bc_react": 0.62, "p_bc_emo": 0.21, "p_turn_yielding": 0.88}
    )

    captured = capsys.readouterr()
    assert "maai_result" in captured.out
    assert "p_yielding='0.88'" in captured.out
    assert "raw_keys=\"['p_bc_emo', 'p_bc_react', 'p_turn_yielding']\"" in captured.out


def test_turn_material_aggregator_merges_split_maai_streams() -> None:
    aggregator = TurnMaterialAggregator(window_ms=200)

    aggregator.observe_maai_result({"p_bc_react": 0.62, "p_bc_emo": 0.21})
    aggregator.observe_maai_result({"p_yielding": 0.88})
    materials = aggregator.snapshot(now_ms=0.0)

    assert materials.p_bc_react == pytest.approx(0.62)
    assert materials.p_bc_emo == pytest.approx(0.21)
    assert materials.p_yielding == pytest.approx(0.88)


def test_tomoko_internal_ws_stores_latest_turn_materials() -> None:
    state = TurnMaterialState()
    tomoko_realtime_app.state.turn_material_state = state
    materials = TurnMaterials(
        window_ms=200,
        user_speaking=True,
        speech_probability=0.72,
        p_yielding=0.9,
        silence_ms=120,
        playback_active=False,
        p_bc_react=0.61,
        stt_partial="今日の予定を",
    )

    with TestClient(tomoko_realtime_app).websocket_connect("/internal/hot-path") as ws:
        ready = ws.receive_json()
        ws.send_json({"type": "turn_materials", **materials.to_dict()})
        ack = ws.receive_json()

    assert ready["type"] == "ready"
    assert ack["type"] == "turn_materials_ack"
    assert state.latest is not None
    assert state.latest.p_yielding == pytest.approx(0.9)
    assert state.latest.stt_partial == "今日の予定を"


def test_tomoko_realtime_fake_personality_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "TOMOKO_V2_FAKE_PERSONALITY",
        (
            '{"talkativeness": 0.9, "curiosity": 0.8, "restraint": 0.1, '
            '"empathy": 0.7, "interrupt_tolerance": 0.6}'
        ),
    )

    personality = _fake_personality_materials()

    assert personality is not None
    assert personality.talkativeness == pytest.approx(0.9)
    assert personality.curiosity == pytest.approx(0.8)
    assert personality.restraint == pytest.approx(0.1)
    assert personality.empathy == pytest.approx(0.7)
    assert personality.interrupt_tolerance == pytest.approx(0.6)


def test_tomoko_realtime_fake_user_status_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "TOMOKO_V2_FAKE_USER_STATUS",
        (
            '{"present": false, "activity_label": "away", '
            '"summary": "away: no user detected", "confidence": 0.95}'
        ),
    )

    observation = _fake_user_status_observation()

    assert observation is not None
    assert observation.present is False
    assert observation.activity_label == "away"
    assert observation.confidence == pytest.approx(0.95)


def test_tomoko_internal_ws_updates_user_status_materials() -> None:
    state = TurnMaterialState()
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(["了解。"]),
    )
    status = UserStatusObservation(
        present=False,
        activity_label="away",
        summary="away: no user detected",
        source="unit",
        confidence=0.95,
    )
    tomoko_realtime_app.state.turn_material_state = state
    tomoko_realtime_app.state.conversation_core = core

    with TestClient(tomoko_realtime_app).websocket_connect("/internal/hot-path") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_status", **status.to_dict()})
        ack = ws.receive_json()

    assert ack["type"] == "user_status_ack"
    assert ack["user_status_id"] == str(status.id)
    assert core.user_status is not None
    assert core.user_status.present is False
    assert core.world_materials.user_present is False
    assert core.world_materials.user_status_confidence == pytest.approx(0.95)


@pytest.mark.asyncio
async def test_tomoko_realtime_refreshes_db_candidates_into_core(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = CandidateRecord(
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

    class FakeCache:
        async def active_candidates(self) -> list[CandidateRecord]:
            return [candidate]

    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(["了解。"]),
    )
    monkeypatch.setattr(tomoko_realtime, "_db_candidate_cache", lambda: FakeCache())

    await tomoko_realtime._refresh_db_candidates_if_enabled(core)

    assert core.candidate_records == [candidate]


def test_tomoko_internal_ws_turns_stt_observation_into_speech_order() -> None:
    state = TurnMaterialState()
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(["了解。"]),
    )
    tomoko_realtime_app.state.turn_material_state = state
    tomoko_realtime_app.state.conversation_core = core
    materials = TurnMaterials(
        window_ms=200,
        user_speaking=False,
        speech_probability=0.0,
        p_yielding=0.95,
        silence_ms=600,
        playback_active=False,
        p_bc_react=0.6,
        p_bc_emo=0.2,
    )
    now = utc_now()
    observation = PartialTranscriptObservation(
        text="トモコ、短く返事して",
        is_final=True,
        stability=1.0,
        audio_started_at=now,
        audio_ended_at=now,
    )

    with TestClient(tomoko_realtime_app).websocket_connect("/internal/hot-path") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "turn_materials", **materials.to_dict()})
        assert ws.receive_json()["type"] == "turn_materials_ack"
        ws.send_json({"type": "stt_observation", **observation.to_dict()})
        ack = ws.receive_json()
        order_event = ws.receive_json()

    assert ack["type"] == "stt_observation_ack"
    assert ack["observation_id"] == str(observation.id)
    assert ack["score"] > 0
    assert ack["reason"] == "prepared speech crossed emit threshold"
    assert ack["score_breakdown"]
    assert ack["score_breakdown"]["pressure_natural_backchannel_desire"] > 0
    assert ack["score_breakdown"]["pressure_natural_light_reaction_desire"] > 0
    assert ack["p_yielding"] == pytest.approx(0.95)
    assert order_event["type"] == "speech_order"
    assert order_event["text"] == "了解。"
    assert order_event["mode"] == "replace_current"


def test_playback_state_inactive_clears_core_current_speech_order() -> None:
    state = TurnMaterialState()
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(["了解。"]),
    )
    core.current_speech_order = SpeechOrder(
        text="話し中の返答",
        mode=SpeechOrderMode.REPLACE_CURRENT,
        reason="test current speech",
        priority=60,
        response_kind=ResponseKind.CONTENT,
    )
    core.current_speech_score = 0.9
    tomoko_realtime_app.state.turn_material_state = state
    tomoko_realtime_app.state.conversation_core = core

    with TestClient(tomoko_realtime_app).websocket_connect("/internal/hot-path") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "playback_state", "playback_active": False})
        ack = ws.receive_json()

    assert ack["type"] == "playback_state_ack"
    assert core.current_speech_order is None
    assert core.current_speech_score == 0.0


def test_tomoko_internal_ws_can_reset_conversation_state() -> None:
    state = TurnMaterialState()
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(["了解。"]),
    )
    core.current_speech_order = SpeechOrder(
        text="話し中の返答",
        mode=SpeechOrderMode.REPLACE_CURRENT,
        reason="test current speech",
        priority=60,
        response_kind=ResponseKind.CONTENT,
    )
    tomoko_realtime_app.state.turn_material_state = state
    tomoko_realtime_app.state.conversation_core = core

    with TestClient(tomoko_realtime_app).websocket_connect("/internal/hot-path") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "reset_conversation"})
        ack = ws.receive_json()

    assert ack["type"] == "reset_conversation_ack"
    assert tomoko_realtime_app.state.conversation_core is not core
    assert tomoko_realtime_app.state.turn_material_state is not state


def test_tomoko_internal_ws_accepts_scenario_calendar_fixture() -> None:
    state = TurnMaterialState()
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(["了解。"]),
    )
    tomoko_realtime_app.state.turn_material_state = state
    tomoko_realtime_app.state.conversation_core = core

    with TestClient(tomoko_realtime_app).websocket_connect("/internal/hot-path") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json(
            {
                "type": "scenario_fixture",
                "fake_calendar": [{"offset_min": 5, "title": "定例会議"}],
            }
        )
        ack = ws.receive_json()

    assert ack["type"] == "scenario_fixture_ack"
    assert ack["calendar_items"] == 1
    assert core.calendar_items_provider is not None
    assert list(core.calendar_items_provider().values()) == ["定例会議"]


async def test_remote_tomoko_ws_core_reconnects_stale_connection_on_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeWebSocket:
        def __init__(self, *, fail_send: bool = False) -> None:
            self.fail_send = fail_send
            self.sent: list[str] = []
            self.responses = [
                '{"type":"ready","process":"tomoko-realtime"}',
                '{"type":"reset_conversation_ack"}',
            ]

        async def send(self, message: str) -> None:
            if self.fail_send:
                raise websockets.exceptions.ConnectionClosedError(None, None, None)
            self.sent.append(message)

        async def recv(self) -> str:
            return self.responses.pop(0)

        async def close(self) -> None:
            return None

    stale = FakeWebSocket(fail_send=True)
    fresh = FakeWebSocket()
    connections = [stale, fresh]

    async def fake_connect(_url: str) -> FakeWebSocket:
        return connections.pop(0)

    monkeypatch.setattr(ws_control.websockets, "connect", fake_connect)
    core = RemoteTomokoWsCore(url="ws://tomoko.test/internal/hot-path")

    await core.reset_conversation()

    assert stale.sent == []
    assert len(fresh.sent) == 1
    assert '"type": "reset_conversation"' in fresh.sent[0]
    assert core._ws is fresh


def test_cancel_order_event_becomes_executable_stop_order() -> None:
    trace_id = uuid4()
    order_id = uuid4()

    order = stop_order_from_cancel_event(
        {
            "type": "cancel_order",
            "order_id": str(order_id),
            "reason": "stop intent crossed emission threshold",
        },
        trace_id=trace_id,
    )

    assert order.mode == SpeechOrderMode.STOP
    assert order.id == order_id
    assert order.trace_id == trace_id
    assert order.reason == "stop intent crossed emission threshold"
    assert order.text == ""


def test_turn_material_state_is_async_safe() -> None:
    async def run() -> None:
        state = TurnMaterialState()
        materials = TurnMaterials(
            window_ms=200,
            user_speaking=False,
            speech_probability=0.0,
            p_yielding=0.4,
            silence_ms=800,
            playback_active=False,
        )
        await state.update(materials)
        assert await state.get_latest() == materials

    asyncio.run(run())
