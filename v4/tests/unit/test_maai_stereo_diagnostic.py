"""Stereo contrast diagnostics use synthetic WAVs and a fake synchronous model."""

from __future__ import annotations

import hashlib
import json
import queue
import sys

import numpy as np
import pytest
import soundfile as sf

from scripts import diagnose_maai_stereo as probe

pytestmark = pytest.mark.unit


class FakeModel:
    """MOCK: record stereo inputs and emit numeric results without loading MaAI."""

    audio_samples_per_frame = 2

    def __init__(self, mode="vap"):
        self.mode = mode
        self.result_dict_queue = queue.Queue()
        self.resets = 0
        self.stopped = False
        self.seen = []

    def reset_runtime_state(self):
        self.resets += 1
        self.result_dict_queue.put({"stale": True})

    def process(self, ch1, ch2):
        assert self.result_dict_queue.empty()
        self.seen.append((self.resets, ch1.copy(), ch2.copy()))
        self.result_dict_queue.put({
            "p_now": [0.2, 0.8], "p_future": [0.3, 0.7],
            "vad": [0.4, 0.6], "p_bc_det": [0.9, 0.1],
            "text": "model-text-must-not-be-exported",
        })

    def stop(self, wait=True):
        assert wait
        self.stopped = True


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")


def source(tmp_path, mic, system, *, private=False, input_rate=16000, output_rate=24000):
    run = tmp_path / "synthetic-case"
    run.mkdir()
    (run / "run.json").write_text(json.dumps({
        "private": private, "events": [{"text": "source-text-must-not-be-exported"}],
    }))
    sf.write(run / "input.wav", np.asarray(mic, dtype=np.float32), input_rate, subtype="FLOAT")
    sf.write(run / "output.wav", np.asarray(system, dtype=np.float32), output_rate, subtype="FLOAT")
    return run


def test_contrast_preserves_mic_order_resamples_system_and_resets_each_condition(tmp_path):
    run = source(tmp_path, [0.1, 0.2, 0.3], [0.0, 0.6, 0.9])
    model = FakeModel()
    calls = []

    def factory(mode):
        calls.append(mode)
        return model

    result = probe.probe_runs([run], model_factory=factory)
    assert calls == ["vap"]
    assert model.resets == 2 and model.stopped
    assert len(model.seen) == 4
    expected_mic = [[0.1, 0.2], [0.3, 0.0]]
    for condition in range(2):
        for index in range(2):
            reset, mic, system = model.seen[condition * 2 + index]
            assert reset == condition + 1
            np.testing.assert_allclose(mic, expected_mic[index])
            expected_system = [[0.0, 0.75], [0.0, 0.0]][index] if condition else [0, 0]
            np.testing.assert_allclose(system, expected_system)

    row = result["runs"][0]
    assert row["window_samples"] == 3
    assert "approximation" in result["alignment"]
    for key, name in [("run_json", "run.json"), ("input_wav", "input.wav"),
                      ("output_wav", "output.wav")]:
        assert row["sha256"][key] == hashlib.sha256((run / name).read_bytes()).hexdigest()
    for condition in row["conditions"].values():
        assert condition["input_samples"] == 3
        assert condition["processed_samples"] == 4
        assert condition["processed_frames"] == condition["result_frames"] == 2
        assert condition["tail_padding_samples"] == 1
        assert condition["processing_wall_s"] >= 0
        assert [f["t"] for f in condition["frames"]] == [2 / 16000, 4 / 16000]
        assert set(condition["frames"][0]) == {"t", "p_now", "p_future", "vad"}
        assert condition["frames"][0]["p_now"] == [0.2, 0.8]
    encoded = json.dumps(result, allow_nan=False)
    assert "source-text-must-not-be-exported" not in encoded
    assert "model-text-must-not-be-exported" not in encoded
    assert str(run) not in encoded


def test_receiver_time_crop_does_not_stretch_resampling_and_pads_short_mic(tmp_path):
    run = source(tmp_path, [0.1, 0.2], [0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
    model = FakeModel()
    result = probe.probe_runs(
        [run], start_s=1 / 16000, duration_s=2 / 16000, model_factory=lambda _: model,
    )
    _, mic, system = model.seen[1]
    np.testing.assert_allclose(mic, [0.2, 0.0])
    np.testing.assert_allclose(system, [0.15, 0.3])
    row = result["runs"][0]
    assert row["start_s"] == 1 / 16000
    assert row["conditions"]["system_recorded"]["frames"][0]["t"] == 3 / 16000


@pytest.mark.parametrize("private", [True, None, 0, "false"])
def test_rejects_private_or_missing_consent_before_audio_or_factory(tmp_path, private):
    run = tmp_path / "reject"
    run.mkdir()
    (run / "run.json").write_text(json.dumps({"private": private}))
    with pytest.raises(ValueError, match="private"):
        probe.probe_runs([run], model_factory=lambda _: pytest.fail("factory called"))


def test_requires_offline_before_factory(tmp_path, monkeypatch):
    run = source(tmp_path, [0.1, 0.2], [0.2, 0.3, 0.4])
    monkeypatch.delenv("HF_HUB_OFFLINE")
    with pytest.raises(ValueError, match="HF_HUB_OFFLINE"):
        probe.probe_runs([run], model_factory=lambda _: pytest.fail("factory called"))


@pytest.mark.parametrize("rates", [(8000, 24000), (16000, 16000)])
def test_rejects_unexpected_sample_rates(tmp_path, rates):
    run = source(tmp_path, [0.1, 0.2], [0.2, 0.3], input_rate=rates[0], output_rate=rates[1])
    with pytest.raises(ValueError, match="sample rate"):
        probe.probe_runs([run], model_factory=lambda _: pytest.fail("factory called"))


def test_bc_detection_keeps_both_numeric_channels(tmp_path):
    run = source(tmp_path, [0.1, 0.2], [0.2, 0.3, 0.4])
    model = FakeModel("bc_det")
    result = probe.probe_runs([run], mode="bc_det", model_factory=lambda _: model)
    frame = result["runs"][0]["conditions"]["system_recorded"]["frames"][0]
    assert frame == {"t": 2 / 16000, "p_bc_det": [0.9, 0.1]}


def test_cli_writes_only_sanitized_report_with_fake_model(tmp_path, monkeypatch):
    run = source(tmp_path, [0.1, 0.2], [0.2, 0.3, 0.4])
    target = tmp_path / "probe.json"
    monkeypatch.setattr(probe, "_make_cached_model", lambda _: FakeModel())
    monkeypatch.setattr(sys, "argv", ["probe", str(run), "--out", str(target)])
    probe.main()
    report = json.loads(target.read_text())
    assert report["sample_rate"] == 16000
    assert len(report["runs"]) == 1
    assert "source-text-must-not-be-exported" not in target.read_text()


@pytest.mark.parametrize("kwargs", [{"duration_s": 121}, {"duration_s": 0}, {"start_s": -1}])
def test_rejects_unbounded_windows_before_factory(tmp_path, kwargs):
    with pytest.raises(ValueError):
        probe.probe_runs([tmp_path], model_factory=lambda _: pytest.fail("factory called"), **kwargs)
