"""MOCK: 各信号の入力時刻を保ち、一方の遅れを他方へ移さない。"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from test_engine_maai_session import Rig

from engine.dto import MaaiFrame, MaaiObservation
from engine.sensors.maai import CompositeMaai, MaaiSensor

pytestmark = pytest.mark.unit


def combined(primary_at: float, vap_at: float) -> MaaiFrame:
    primary, vap = Mock(spec=MaaiSensor), Mock(spec=MaaiSensor)
    primary.latest.return_value = MaaiFrame(at_s=primary_at, p_bc=0.8, p_bc_det_user=0.9)
    vap.latest.return_value = MaaiFrame(at_s=vap_at, p_now=(0.2, 0.8), vad_user=0.1)
    return CompositeMaai(primary, vap).latest()


def test_stale_mimi_does_not_hide_fresh_cpc_or_become_fresh_itself() -> None:
    frame = combined(1.0, 1.3)
    assert frame.at_s == 1.0  # 互換の表示時刻をmaxへ変えてごまかさない。
    fresh = frame.fresh_for("vap", now_s=1.3, stale_s=0.25)
    assert fresh is not None and fresh.at_s == 1.3 and fresh.p_now == (0.2, 0.8)
    assert fresh.p_bc is None and fresh.p_bc_det_user is None
    assert frame.fresh_for("bc", now_s=1.3, stale_s=0.25) is None
    assert frame.fresh_for("bc_det", now_s=1.3, stale_s=0.25) is None


def test_stale_cpc_does_not_hide_fresh_mimi_or_supply_stale_vap() -> None:
    frame = combined(1.3, 1.0)
    fresh = frame.fresh_for("bc", now_s=1.3, stale_s=0.25)
    assert fresh is not None and fresh.at_s == 1.3 and fresh.p_bc == 0.8
    assert fresh.p_now is None and fresh.vad_user is None
    assert frame.fresh_for("bc_det", now_s=1.3, stale_s=0.25).p_bc_det_user == 0.9
    assert frame.fresh_for("vap", now_s=1.3, stale_s=0.25) is None


def test_backchannel_pause_check_keeps_the_primary_prediction_time() -> None:
    frame = combined(1.2, 1.3)
    fresh = frame.fresh_for("bc", now_s=1.3, stale_s=0.25)
    assert fresh.at_s == 1.2 and fresh.vap_at_s == 1.3
    assert fresh.at_s < 1.25  # 1.25秒に始まる無音の後の予測には見せない。


def test_legacy_timestamp_missing_signal_and_future_time_contracts() -> None:
    frame = MaaiFrame(at_s=1.1, p_now=(0.1, 0.9), p_bc=0.7)
    assert frame.time_for("vap") == frame.time_for("bc") == 1.1
    assert frame.fresh_for("vap", now_s=1.2, stale_s=0.25) == frame
    assert frame.fresh_for("bc_det", now_s=1.2, stale_s=0.25) is None
    assert frame.fresh_for("vap", now_s=1.0, stale_s=0.25) == frame  # 既存の未来時刻扱いを保つ。
    with pytest.raises(ValueError, match="signal"):
        frame.time_for("unknown")


@pytest.mark.parametrize("selected,retained", [
    (selected, retained)
    for selected in ("vap", "bc", "bc_det")
    for retained in ("vap", "bc", "bc_det")
    if selected != retained
])
def test_reselecting_a_signal_keeps_other_groups_legacy_prediction_time(
    selected: str, retained: str,
) -> None:
    frame = MaaiFrame(at_s=9.8, p_now=(0.1, 0.9), p_bc=0.8, p_bc_det_user=0.9,
                      **{f"{selected}_at_s": 10.0})
    fresh = frame.fresh_for(selected, now_s=10.0, stale_s=0.25)
    assert fresh is not None and fresh.at_s == 10.0
    assert frame.time_for(retained) == fresh.time_for(retained) == 9.8
    assert frame.fresh_for(retained, now_s=10.1, stale_s=0.25) is None
    assert fresh.fresh_for(retained, now_s=10.1, stale_s=0.25) is None


async def test_actual_endpoint_and_guard_read_required_signal_and_log_snapshot() -> None:
    rig = Rig(maai_endpoint=True, barge_guard=True)
    records: list[tuple[str, dict]] = []
    rig.session._trace.event = lambda name, **fields: records.append((name, fields))
    try:
        rig.vad._windows = 50  # 1.6秒。合成時計だけを進める。
        rig.maai.frame = combined(1.2, 1.6)
        assert rig.session._decide_end(0.3) is True
        rig.session._speech_started_while_playing = True
        rig.session._play_end = float("inf")
        assert rig.session._looks_like_backchannel(1.6) is None
        snapshots = [fields for name, fields in records if name == "maai_latest_read"]
        assert [s["signal"] for s in snapshots] == ["vap", "bc_det"]
        assert snapshots[0]["used"]["at_s"] == 1.6
        assert snapshots[0]["raw"]["at_s"] == 1.2
        assert snapshots[1]["used"] is None
    finally:
        await rig.session.close()


async def test_worker_observation_is_forwarded_as_numeric_trace() -> None:
    rig = Rig()
    records = []
    rig.session._trace.event = lambda name, **fields: records.append((name, fields))
    try:
        rig.session.on_maai_observation(MaaiObservation(
            kind="process", source="cpc", at_s=1.2, enqueued_perf_s=10.0,
            dequeued_perf_s=10.1, process_started_perf_s=10.1,
            process_finished_perf_s=10.12, dispatched_perf_s=10.14, output=False))
        assert records[0][0] == "maai_worker"
        assert records[0][1]["source"] == "cpc" and records[0][1]["output"] is False
        assert records[0][1]["enqueued_perf_s"] == 10.0
    finally:
        await rig.session.close()


async def test_decision_snapshot_keeps_full_precision_near_threshold() -> None:
    rig = Rig(maai_endpoint=True)
    records = []
    rig.session._trace.event = lambda name, **fields: records.append((name, fields))
    try:
        rig.maai.frame = MaaiFrame(at_s=0, p_now=(0.39999999, 0.60000001))
        assert rig.session._decide_end(0.3) is True
        snapshot = next(fields for name, fields in records if name == "maai_latest_read")
        assert snapshot["used"]["p_now"][1] == 0.60000001
    finally:
        await rig.session.close()


@pytest.mark.parametrize("probability,accepted", [(0.2496, False), (0.2504, True)])
async def test_guard_snapshot_keeps_exact_threshold_value_but_maai_log_stays_rounded(
    probability: float, accepted: bool,
) -> None:
    rig = Rig(barge_guard=True, guard_bc_det=0.25)
    records = []
    rig.session._trace.event = lambda name, **fields: records.append((name, fields))
    try:
        frame = rig.maai.frame = MaaiFrame(at_s=0, p_bc_det_user=probability)
        rig.session._speech_started_while_playing = True
        rig.session._play_end = float("inf")
        rig.session.on_maai(frame)
        assert rig.session._looks_like_backchannel(0) == (probability if accepted else None)
        snapshot = next(fields for name, fields in records if name == "maai_latest_read")
        assert snapshot["raw"]["p_bc_det_user"] == probability
        assert snapshot["used"]["p_bc_det_user"] == probability
        rounded = next(fields for name, fields in records if name == "maai")
        assert rounded["p_bc_det_user"] == 0.25
    finally:
        await rig.session.close()
