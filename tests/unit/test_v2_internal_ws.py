from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from server.hot_path.turn_materials import TurnMaterialAggregator
from server.shared.models import TurnMaterials
from server.tomoko.realtime import app as tomoko_realtime_app
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
