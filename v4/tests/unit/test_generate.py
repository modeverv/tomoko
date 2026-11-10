import numpy as np
import pytest

from bench.audio import read_wav
from bench.generate.__main__ import build
from bench.scenario import Manifest, Scenario, Step

pytestmark = pytest.mark.unit


def fake_synth(text, voice, cache):
    return np.full(1600 * len(text), 0.1, dtype=np.float32)  # 1 文字 0.1 秒


def test_build_writes_audio_and_annotation_with_pauses(tmp_path):
    sc = Scenario("t2-x", "T2", (Step("あいう[pause 1.5]えお", at=1.0),))
    m = build(sc, tmp_path / "t2-x", tmp_path / "cache", synth=fake_synth)
    a = m.audio[0]
    assert a.duration == pytest.approx(2.0)
    assert a.voiced == [[0.0, 0.3], [1.8, 2.0]]
    assert a.pauses == [[0.3, 1.8]]
    samples, sr = read_wav(tmp_path / "t2-x" / a.wav)
    assert sr == 16000 and len(samples) == 32000
    assert np.all(samples[4800:28800] == 0)
    assert Manifest.load(tmp_path / "t2-x") == m
