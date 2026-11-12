"""工場の引数だけを検証する。実際の MaAI / torch は読み込まない。"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from engine.sensors.maai import MaaiSensor, make_maai_multiple

pytestmark = pytest.mark.unit


def test_factory_uses_two_manual_inputs_shared_mimi_and_limited_threads(monkeypatch):
    # MOCK: import 先を軽い公開 API の代役に置換する。モデルを構築しない。
    settings, threads, inputs = {}, [], []
    model = object()

    def chunk():
        value = object()
        inputs.append(value)
        return value

    def multiple(**kwargs):
        settings.update(kwargs)
        return model

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(set_num_threads=threads.append))
    monkeypatch.setitem(sys.modules, "maai", SimpleNamespace(
        MaaiInput=SimpleNamespace(Chunk=chunk), MaaiMultiple=multiple,
    ))
    assert make_maai_multiple(("vap", "bc", "bc_det"), torch_threads=2) is model
    assert settings["configs"] == [
        {"mode": "vap", "lang": "jp"}, {"mode": "bc", "lang": "jp"},
        {"mode": "bc_det", "lang": "jp"},
    ]
    assert settings["audio_ch1"] is inputs[0] and settings["audio_ch2"] is inputs[1]
    assert inputs[0] is not inputs[1]
    assert (settings["model_type"], settings["frame_rate"], settings["context_len_sec"]) == (
        "normal-ver2", 12.5, 20,
    )
    assert settings["device"] == "cpu" and threads == [2]
    assert settings["mimi_onnx_cpu_intra_threads"] == 2
    assert settings["mimi_onnx_cpu_inter_threads"] == 1


@pytest.mark.parametrize("kwargs", [
    {"modes": ()}, {"modes": ("vap", "vap")}, {"torch_threads": 0},
])
def test_factory_rejects_bad_settings_before_importing_models(kwargs):
    with pytest.raises(ValueError):
        make_maai_multiple(**kwargs)
    assert "maai" not in sys.modules and "torch" not in sys.modules


def test_inference_failure_keeps_the_primary_error_when_stop_also_fails(caplog):
    primary, secondary = ValueError("inference failed"), RuntimeError("stop failed")

    class BrokenModel:
        """MOCK: 実モデルを使わず推論と終了の二重障害を起こす。"""

        def process(self, _ch1, _ch2):
            raise primary

        def stop(self, wait=True):
            raise secondary

    sensor = MaaiSensor(BrokenModel)
    try:
        sensor.feed_mic([0] * 1280)
        sensor._thread.join(2)
        assert not sensor._thread.is_alive()
        assert sensor.error is primary
        assert "inference failed" in caplog.text and "stop failed" in caplog.text
    finally:
        sensor.close()


def test_factory_failure_is_observable_and_does_not_leave_a_thread():
    error = RuntimeError("factory failed")

    def broken_factory():
        raise error

    sensor = MaaiSensor(broken_factory)
    try:
        sensor._thread.join(2)
        assert not sensor._thread.is_alive() and sensor.error is error
        assert sensor.latest() is None
        with pytest.raises(RuntimeError, match="failed"):
            sensor.feed_mic([])
    finally:
        sensor.close()


def test_mismatched_model_frame_size_fails_before_inference():
    stopped = []
    # MOCK: frame rate 不一致のモデル。process は呼べない形で渡す。
    model = SimpleNamespace(audio_samples_per_frame=1600, stop=lambda wait: stopped.append(wait))
    sensor = MaaiSensor(lambda: model)
    try:
        sensor._thread.join(2)
        assert isinstance(sensor.error, ValueError)
        assert stopped == [True]
    finally:
        sensor.close()
