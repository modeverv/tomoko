"""同期推論と旧CLI互換性の試験。MaAI は読み込まない。"""

from __future__ import annotations

import json
import queue
import sys
from importlib.metadata import PackageNotFoundError

import numpy as np
import pytest

from bench.audio import write_wav
from bench.generate.__main__ import build
from bench.scenario import Scenario, Step
from scripts.maai_offline import at, main, model_settings, run_model, run_vap

pytestmark = pytest.mark.unit


def generated_source(path):
    scenario = Scenario("example", "T5", (Step("続く、[pause 0.5]終わり。", at=0.0),))
    build(
        scenario,
        path / "example",
        path / "cache",
        synth=lambda _text, _voice, _cache: np.full(16000, 0.2, dtype=np.float32),
    )
    return path


class FakeModel:
    """MOCK: 単体テスト用の同期推論。MaAI は読み込まない。"""

    audio_samples_per_frame = 1600
    variant = "synthetic-test"

    def __init__(self):
        self.result_dict_queue = queue.Queue()
        self.seen = []
        self.resets = 0

    def reset_runtime_state(self):
        self.resets += 1

    def process(self, ch1, ch2):
        assert self.result_dict_queue.empty()  # 次のフレームの前に結果を必ず読む
        self.seen.append((ch1.copy(), ch2.copy()))
        self.result_dict_queue.put(
            {
                "p_now": [0.1, 0.9],
                "p_future": [0.2, 0.8],
                "p_bc": 0.7,
                "p_bc_det": [0.6, 0.4],
                "p_bc_react": 0.3,
                "p_bc_emo": 0.2,
            }
        )

    def stop(self, wait):
        assert wait


@pytest.mark.parametrize(
    "mode,key,expected,swapped",
    [
        ("vap", "p_now", 0.9, False),
        ("bc", "p_bc", 0.7, True),
        ("bc_2type", "p_bc_react", 0.3, True),
        ("bc_det", "p_bc_det", 0.6, False),
    ],
)
def test_stream_channels_result_axes_and_sample_clock(mode, key, expected, swapped):
    model = FakeModel()
    model.result_dict_queue.put({"stale": True})
    frames = run_model(model, np.ones(3300, dtype=np.float32), mode)
    assert model.resets == 1
    assert [f["t"] for f in frames] == [0.1, 0.2]
    assert [f[key] for f in frames] == [expected, expected]
    assert len(model.seen) == 2
    for ch1, ch2 in model.seen:
        assert np.all(ch1 == (0 if swapped else 1))
        assert np.all(ch2 == (1 if swapped else 0))


def test_bc_user_channel_can_be_flipped_to_test_the_orientation():
    model = FakeModel()
    run_model(model, np.ones(1600, dtype=np.float32), "bc", True)
    ch1, ch2 = model.seen[0]
    assert np.all(ch1 == 1) and np.all(ch2 == 0)


@pytest.mark.parametrize(
    "mode,encoder,expected",
    [
        ("vap", "cpc", ("normal", 10, 5)),
        ("bc", "cpc", ("normal", 10, 5)),
        ("bc_det", "cpc", ("normal-ver2", 12.5, 20)),  # bc_det の日本語の重みは Mimi だけ
        ("vap", "mimi", ("normal-ver2", 12.5, 20)),
        ("bc", "mimi", ("normal-ver2", 12.5, 20)),
    ],
)
def test_model_settings_per_mode(mode, encoder, expected):
    assert model_settings(mode, 10, encoder) == expected


def test_bc_2type_has_no_mimi_weights():
    with pytest.raises(ValueError, match="bc_2type"):
        model_settings("bc_2type", 10, "mimi")


def test_missing_result_is_an_error_instead_of_silently_skipping():
    model = FakeModel()
    model.process = lambda _a, _b: None
    with pytest.raises(RuntimeError, match="結果"):
        run_model(model, np.zeros(1600, dtype=np.float32), "vap")


def test_legacy_vap_helpers_keep_their_return_types(monkeypatch):
    monkeypatch.setattr("scripts.maai_offline.make_model", lambda *args: FakeModel())
    series = run_vap(np.ones(3200, dtype=np.float32), "jp", 10)
    assert series == [(0.1, 0.9, 0.8), (0.2, 0.9, 0.8)]
    assert at(series, 0.09) is None
    assert at(series, 0.15) == (0.9, 0.8)


def test_cli_generated_modes_save_aggregates_and_tables(tmp_path, monkeypatch, capsys):
    inputs = generated_source(tmp_path)
    output = tmp_path / "result.json"
    monkeypatch.setattr("scripts.maai_offline.make_model", lambda *args: FakeModel())

    def unavailable_version(_name):
        raise PackageNotFoundError("maai")

    monkeypatch.setattr("scripts.maai_offline.version", unavailable_version)
    monkeypatch.setattr(
        sys, "argv", ["maai_offline", str(inputs), "--out", str(output), "--modes", "vap,bc,bc_det"]
    )
    main()
    report = json.loads(output.read_text())
    assert report["source_kind"] == "inputs"
    assert set(report["summary"]) == {"vap", "bc", "bc_det"}
    assert report["summary"]["bc_det"]["auc"] is None
    assert report["settings"]["torch_threads"] == 2
    assert report["settings"]["maai_version"] is None
    assert report["timing"]["vap"]["model"]["variant"] == "synthetic-test"
    text = capsys.readouterr().out
    assert "AUC" in text and "pause_comma" in text and "bc_det" in text
    assert "maai" not in sys.modules


def test_cli_existing_run_usage_keeps_json_row_list(tmp_path, monkeypatch):
    from bench.score import segments

    directory = tmp_path / "old"
    directory.mkdir()
    (directory / "run.json").write_text(
        json.dumps({"id": "old", "axis": "T2", "steps": [{"start": 0.0, "end": 2.0}]})
    )
    write_wav(directory / "input.wav", np.ones(64000, dtype=np.float32), 16000)
    monkeypatch.setattr(segments, "speech_segments", lambda _p: [(0.0, 0.5), (1.0, 2.0)])
    monkeypatch.setattr("scripts.maai_offline.make_model", lambda *args: FakeModel())
    output = tmp_path / "old.json"
    monkeypatch.setattr(sys, "argv", ["maai_offline", str(tmp_path), "--out", str(output)])
    main()
    rows = json.loads(output.read_text())
    assert isinstance(rows, list)
    assert [r["kind"] for r in rows] == ["hold", "shift"]
    assert rows[0]["d0.1"] == [0.9, 0.8]
