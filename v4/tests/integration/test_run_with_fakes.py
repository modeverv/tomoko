"""実行と偽のシステムを、手元の WebSocket で実際につなぐ。VAD と TTS は使わない。"""

import json

import numpy as np
import pytest

from bench import definitions as d
from bench.audio import read_wav
from bench.fakes.server import serve
from bench.generate.__main__ import build
from bench.run import run_scenario
from bench.scenario import Scenario, Step

pytestmark = pytest.mark.integration
CLIP = np.full(d.OUT_SR * 12, 0.2, dtype=np.float32)


def tone(text, voice, cache):
    return np.full(int(d.IN_SR * 0.1 * len(text)), 0.2, dtype=np.float32)


def loud_spans(samples, sr):
    """音のある区間を、秒で返す。"""
    on = np.abs(samples) > 0.01
    edges = np.flatnonzero(np.diff(np.concatenate([[0], on.astype(np.int8), [0]])))
    spans = []
    for a, b in zip(edges[::2] / sr, edges[1::2] / sr):
        if spans and a - spans[-1][1] < 0.05:
            spans[-1] = (spans[-1][0], b)
        else:
            spans.append((a, b))
    return spans


async def go(kind, scenario, tmp_path):
    build(scenario, tmp_path / "in", tmp_path / "cache", synth=tone)
    server = await serve(kind, CLIP)
    port = server.sockets[0].getsockname()[1]
    try:
        rec = await run_scenario(f"ws://127.0.0.1:{port}/ws", tmp_path / "in", tmp_path / "out")
    finally:
        server.close()
    out, sr = read_wav(tmp_path / "out" / "output.wav")
    assert json.loads((tmp_path / "out" / "run.json").read_text())["id"] == scenario.id
    return rec, loud_spans(out, sr)


async def test_clockwork_speaks_half_a_second_after_the_input_ends(tmp_path):
    sc = Scenario("t1", "T1", (Step("あいうえおかきくけこ", at=0.5),))
    rec, spans = await go("clockwork", sc, tmp_path)
    end = rec["steps"][0]["end"]
    assert end == pytest.approx(1.5)
    assert len(spans) == 1
    assert spans[0][0] - end == pytest.approx(0.5, abs=0.06)
    assert spans[0][1] - spans[0][0] == pytest.approx(2.0, abs=0.05)
    assert rec["error"] is None and not rec["precondition_failed"]


async def test_silent_system_fails_the_precondition_of_an_interrupt(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "TRIGGER_TIMEOUT_S", 2.0)
    sc = Scenario("t4", "T4", (Step("あいうえお", at=0.5), Step("かきく", after_tomoko_speaks=1.0)))
    rec, spans = await go("silent", sc, tmp_path)
    assert rec["precondition_failed"] and len(rec["steps"]) == 1 and spans == []


async def test_polite_system_stops_when_interrupted_and_answers_again(tmp_path):
    sc = Scenario("t4", "T4", (Step("あいうえお", at=0.5),
                               Step("かきくけこさしすせそ", after_tomoko_speaks=1.5)))
    rec, spans = await go("polite", sc, tmp_path)
    first, cut = rec["steps"]
    assert spans[0][0] - first["end"] == pytest.approx(0.5, abs=0.06)
    assert cut["start"] - spans[0][0] == pytest.approx(1.5, abs=0.15)
    assert spans[0][1] - cut["start"] == pytest.approx(0.3, abs=0.08)  # 0.3 秒で止まる
    assert spans[1][0] - cut["end"] == pytest.approx(0.8, abs=0.06)
    stops = [e for e in rec["events"] if e.get("type") == "stop"]
    assert len(stops) == 1 and rec["stale_chunks"] == 0


async def test_follow_up_step_waits_for_the_reply_to_finish(tmp_path):
    sc = Scenario("c2", "C2", (Step("あいうえお", at=0.5), Step("かきくけこ", after_reply=1.0)))
    rec, spans = await go("clockwork", sc, tmp_path)
    assert len(rec["steps"]) == 2 and len(spans) == 2
    assert rec["steps"][1]["start"] - spans[0][1] == pytest.approx(1.0, abs=0.15)
