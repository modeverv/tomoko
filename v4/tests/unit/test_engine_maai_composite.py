"""MOCK: 2 センサーの統合契約を、モデルも作業スレッドも使わず確認する。"""

from __future__ import annotations

from unittest.mock import Mock

import numpy as np
import pytest

from engine.dto import MaaiFrame
from engine.sensors import maai

pytestmark = pytest.mark.unit


def sensor(frame=None, *, dropped=0, error=None):
    fake = Mock(spec=maai.MaaiSensor)
    fake.latest.return_value = frame
    fake.feed_mic.return_value = dropped
    fake.dropped_frames = dropped
    fake.error = error
    return fake


@pytest.mark.parametrize("primary_at,vap_at", [(1.12, 1.1), (1.04, 1.1)])
def test_latest_uses_primary_backchannel_cpc_vap_and_older_time(primary_at, vap_at):
    primary = sensor(MaaiFrame(
        at_s=primary_at, p_bc=0.7, p_bc_det_user=0.8,
        p_now=(0.9, 0.1), p_future=(0.8, 0.2), vad_user=0.01, vad_tomoko=0.02,
    ))
    vap = sensor(MaaiFrame(
        at_s=vap_at, p_now=(0.3, 0.7), p_future=(0.4, 0.6),
        vad_user=0.9, vad_tomoko=0.1, p_bc=0.01, p_bc_det_user=0.02,
    ))
    combined = maai.CompositeMaai(primary, vap)
    assert combined.latest() == MaaiFrame(
        at_s=min(primary_at, vap_at), p_bc=0.7, p_bc_det_user=0.8,
        p_now=(0.3, 0.7), p_future=(0.4, 0.6), vad_user=0.9, vad_tomoko=0.1,
        vap_at_s=vap_at, bc_at_s=primary_at, bc_det_at_s=primary_at,
    )
    # キャッシュせず、2 つの最新の予測を取り直す。
    vap.latest.return_value = MaaiFrame(at_s=1.2, p_now=(0.2, 0.8))
    assert combined.latest() == MaaiFrame(
        at_s=primary_at, p_bc=0.7, p_bc_det_user=0.8, p_now=(0.2, 0.8),
        vap_at_s=1.2, bc_at_s=primary_at, bc_det_at_s=primary_at,
    )


@pytest.mark.parametrize("primary_frame,vap_frame", [
    (None, None),
    (MaaiFrame(at_s=0.08, p_bc=0.6, p_bc_det_user=0.4), None),
    (None, MaaiFrame(at_s=0.1, p_now=(0.2, 0.8), vad_user=0.9)),
])
def test_latest_with_initial_missing_frames(primary_frame, vap_frame):
    combined = maai.CompositeMaai(sensor(primary_frame), sensor(vap_frame))
    assert combined.latest() == (primary_frame if vap_frame is None else vap_frame)


def test_missing_values_are_not_filled_from_the_wrong_model():
    primary = sensor(MaaiFrame(at_s=0.16, p_now=(0.9, 0.1)))
    vap = sensor(MaaiFrame(at_s=0.2, p_bc=0.8))
    assert maai.CompositeMaai(primary, vap).latest() == MaaiFrame(at_s=0.16)


def test_audio_stop_and_close_reach_both_sensors_and_drops_are_summed():
    primary, vap = sensor(dropped=2), sensor(dropped=3)
    combined = maai.CompositeMaai(primary, vap)
    mic = np.arange(512, dtype=np.float32)
    voice = np.arange(720, dtype=np.float32)
    assert combined.feed_mic(mic) == 5
    assert combined.dropped_frames == 5
    primary.dropped_frames, vap.dropped_frames = 4, 7
    assert combined.dropped_frames == 11
    combined.add_tomoko_audio(voice, at_mic_s=1.25)
    combined.stop_tomoko(at_mic_s=1.4)
    combined.close(timeout=0.3)
    for fake in (primary, vap):
        assert fake.feed_mic.call_args.args[0] is mic
        args, kwargs = fake.add_tomoko_audio.call_args
        assert args[0] is voice
        assert (args[1] if len(args) > 1 else kwargs["at_mic_s"]) == 1.25
        args, kwargs = fake.stop_tomoko.call_args
        assert (args[0] if args else kwargs["at_mic_s"]) == 1.4
        args, kwargs = fake.close.call_args
        assert (args[0] if args else kwargs["timeout"]) == 0.3


@pytest.mark.parametrize("primary_fails,vap_fails", [(True, False), (False, True), (True, True)])
def test_close_attempts_both_even_on_failure_and_preserves_first_error(primary_fails, vap_fails):
    primary_error, vap_error = TimeoutError("Mimi close"), RuntimeError("CPC close")
    primary, vap = sensor(), sensor()
    primary.close.side_effect = primary_error if primary_fails else None
    vap.close.side_effect = vap_error if vap_fails else None
    with pytest.raises(Exception) as caught:
        maai.CompositeMaai(primary, vap).close()
    assert caught.value is (primary_error if primary_fails else vap_error)
    primary.close.assert_called_once()
    vap.close.assert_called_once()


@pytest.mark.parametrize("primary_fails,vap_fails", [
    (False, False), (True, False), (False, True), (True, True),
])
def test_error_exposes_either_sensor_failure_with_primary_precedence(primary_fails, vap_fails):
    primary_error, vap_error = ValueError("Mimi"), RuntimeError("CPC")
    primary, vap = sensor(), sensor()
    combined = maai.CompositeMaai(primary, vap)
    assert combined.error is None
    primary.error = primary_error if primary_fails else None
    vap.error = vap_error if vap_fails else None
    assert combined.error is (
        primary_error if primary_fails else vap_error if vap_fails else None
    )
