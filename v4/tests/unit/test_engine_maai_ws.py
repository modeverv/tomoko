"""MOCK: 起動と /ws の配線だけを、外部プロセス・モデル・通信なしで確認する。"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from engine import ws as engine_ws
from engine.dto import EngineConfig

pytestmark = pytest.mark.unit


def test_cpc_vap_is_disabled_by_default():
    assert EngineConfig().maai_vap_cpc is False


@pytest.mark.parametrize("raw,expected", [("true", True), ("0", False)])
def test_cpc_vap_can_be_set_from_env(monkeypatch, raw, expected):
    monkeypatch.setenv("TOMOKO_MAAI_VAP_CPC", raw)
    assert EngineConfig.from_env().maai_vap_cpc is expected


@pytest.fixture
def factories(monkeypatch):
    multiple = Mock(return_value=object())
    single = Mock(return_value=object())
    shared = Mock(side_effect=lambda model: model)
    monkeypatch.setattr(engine_ws, "make_maai_multiple", multiple)
    monkeypatch.setattr(engine_ws, "make_maai_single", single, raising=False)
    monkeypatch.setattr(engine_ws, "SharedMaai", shared)

    def load(models):
        models.transcribe = object()
        models.synthesize = object()

    async def run(models, fn, *args):
        return fn(*args)

    async def to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(engine_ws.Models, "load", load)
    monkeypatch.setattr(engine_ws.Models, "run", run)
    monkeypatch.setattr(engine_ws.asyncio, "to_thread", to_thread)
    return multiple, single, shared


@pytest.mark.parametrize("enabled,modes,expected_modes", [
    (False, " vap, bc,bc_det, ", ("vap", "bc", "bc_det")),
    (True, " vap, bc,bc_det, ", ("bc", "bc_det")),
    (True, "vap", ()),
])
async def test_models_create_separate_shared_cpc_only_when_enabled(
    factories, enabled, modes, expected_modes,
):
    multiple, single, shared = factories
    models = engine_ws.Models(EngineConfig(
        maai=True, maai_vap_cpc=enabled, maai_modes=modes, maai_threads=3, tts="kokoro",
    ))
    try:
        await models.start()
        if expected_modes:
            multiple.assert_called_once_with(expected_modes, torch_threads=3)
            assert models.maai is multiple.return_value
        else:
            multiple.assert_not_called()
            assert models.maai is None
        if enabled:
            single.assert_called_once_with(torch_threads=3)
            assert models.maai_vap is single.return_value
        else:
            single.assert_not_called()
            assert models.maai_vap is None
        assert shared.call_count == bool(expected_modes) + enabled
        assert {call.args[0] for call in shared.call_args_list} == (
            ({multiple.return_value} if expected_modes else set())
            | ({single.return_value} if enabled else set())
        )
    finally:
        await models.http.aclose()
        models.mlx.shutdown()


async def test_disabled_maai_does_not_construct_either_model(factories):
    models = engine_ws.Models(EngineConfig(maai=False, maai_vap_cpc=True, tts="kokoro"))
    try:
        await models.start()
        for factory in factories:
            factory.assert_not_called()
        assert models.maai is None and models.maai_vap is None
    finally:
        await models.http.aclose()
        models.mlx.shutdown()


@pytest.mark.parametrize("enabled,cpc_only", [(False, False), (True, False), (True, True)])
async def test_connection_attaches_both_sensors_with_matching_clock_channels_and_callbacks(
    monkeypatch, enabled, cpc_only,
):
    cfg = EngineConfig(maai=True, maai_vap_cpc=enabled, maai_user_channel=1)
    primary = SimpleNamespace(lease=Mock(), frame_samples=1280)
    vap = SimpleNamespace(lease=Mock(), frame_samples=1600)
    models = SimpleNamespace(
        cfg=cfg, maai=None if cpc_only else primary, maai_vap=vap if enabled else None,
        transcribe=object(), synthesize=object(), synthesize_stream=None,
        backchannel_clip=None, http=object(),
    )
    session = SimpleNamespace(on_maai=Mock(), attach_maai=Mock(), close=AsyncMock())
    sensors = [object(), object()]
    sensor = Mock(side_effect=sensors)
    composite = Mock(return_value=object())
    thread_counts = []
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(set_num_threads=thread_counts.append))
    monkeypatch.setattr(engine_ws, "silero_prob", lambda: object())
    monkeypatch.setattr(engine_ws, "VadSensor", Mock())
    monkeypatch.setattr(engine_ws, "Trace", Mock())
    monkeypatch.setattr(engine_ws, "BaselineSession", Mock(return_value=session))
    monkeypatch.setattr(engine_ws, "MaaiSensor", sensor)
    monkeypatch.setattr(engine_ws, "CompositeMaai", composite, raising=False)
    socket = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(models=models)), accept=AsyncMock(),
        receive=AsyncMock(return_value={"type": "websocket.disconnect"}),
    )
    await engine_ws.ws(socket)
    expected_shared = [primary, vap] if enabled and not cpc_only else [vap if cpc_only else primary]
    assert sensor.call_count == len(expected_shared)
    for call, shared in zip(sensor.call_args_list, expected_shared, strict=True):
        assert call.args == (shared.lease,)
        assert call.kwargs == {
            "frame_samples": shared.frame_samples,
            "user_channel": 0 if enabled else cfg.maai_user_channel,
            "on_frame": session.on_maai,
            "loop": asyncio.get_running_loop(),
        }
    if enabled and not cpc_only:
        composite.assert_called_once_with(*sensors)
        session.attach_maai.assert_called_once_with(composite.return_value)
    else:
        composite.assert_not_called()
        session.attach_maai.assert_called_once_with(sensors[0])
    assert thread_counts == [cfg.maai_threads]
    session.close.assert_awaited_once()
