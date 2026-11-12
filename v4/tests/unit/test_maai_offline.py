"""MaAI を使わず、合成の注釈と予測列だけで評価器を試す。"""

from __future__ import annotations

import json
import sys

import numpy as np
import pytest

from bench.audio import write_wav
from bench.maai_input import load_clips
from bench.score.maai import auc, distribution, evaluate, summarize

pytestmark = pytest.mark.unit


def manifest(tmp_path, axis="T5", *, rate=16000):
    directory = tmp_path / "example"
    directory.mkdir()
    steps = [{"say": "続くけど、[pause 0.5]次。[pause 0.2]最後。", "at": 0.0}]
    audio = [
        {
            "wav": "step0.wav",
            "duration": 2.0,
            "voiced": [[0, 0.5], [1.0, 1.5], [1.7, 2.0]],
            "pauses": [[0.5, 1.0], [1.5, 1.7]],
        }
    ]
    if axis in {"T3", "T4"}:
        steps.append({"say": "なるほど" if axis == "T3" else "待って", "after_tomoko_speaks": 1.0})
        audio.append({"wav": "step1.wav", "duration": 0.3, "voiced": [[0.0, 0.3]], "pauses": []})
        write_wav(directory / "step1.wav", np.full(4800, 0.3), rate)
    raw = {
        "scenario": {
            "id": "example",
            "axis": axis,
            "steps": steps,
            "voice": "synthetic",
            "expect": {},
        },
        "audio": audio,
    }
    (directory / "scenario.json").write_text(json.dumps(raw))
    write_wav(directory / "step0.wav", np.full(32000, 0.25), rate)
    return tmp_path


@pytest.mark.parametrize(
    "positive,negative,expected",
    [
        ([0.8, 0.9], [0.1, 0.2], 1.0),
        ([0.1], [0.9], 0.0),
        ([0.5], [0.5], 0.5),
        ([0.5, 0.8], [0.5, 0.6], 0.625),
        ([], [0.1], None),
        ([0.1], [], None),
    ],
)
def test_auc_uses_positive_class_and_half_credit_for_ties(positive, negative, expected):
    assert auc(positive, negative) == expected


def test_distribution_handles_zero_and_one_item():
    assert distribution([]) == {"n": 0, "mean": None, "p10": None, "median": None, "p90": None}
    assert distribution([0.3]) == {"n": 1, "mean": 0.3, "p10": 0.3, "median": 0.3, "p90": 0.3}


def test_generated_clip_padding_and_annotation_offsets(tmp_path):
    (clip,) = load_clips(manifest(tmp_path), {"T5"})
    assert len(clip.audio) == 5 * 16000
    assert np.all(clip.audio[:16000] == 0)
    assert np.all(clip.audio[-32000:] == 0)
    assert clip.voiced == [(1.0, 1.5), (2.0, 2.5), (2.7, 3.0)]
    assert [(r["kind"], r["t"], r["gap"]) for r in clip.events] == [
        ("hold", 1.5, 0.5),
        ("hold", 2.5, 0.2),
        ("shift", 3.0, None),
    ]
    assert [r["pause_type"] for r in clip.events if r["kind"] == "hold"] == ["comma", "period"]


@pytest.mark.parametrize("axis", ["T3", "T4"])
def test_selects_first_and_last_utterance_for_detection(tmp_path, axis):
    clips = load_clips(manifest(tmp_path, axis), {axis})
    assert [c.step for c in clips] == [0, 1]
    assert clips[0].detection_voiced == []
    assert clips[1].detection_voiced == [(1.0, 1.3)]
    assert load_clips(tmp_path, {"T2"}) == []


def test_rejects_wrong_sample_rate_and_empty_source(tmp_path):
    with pytest.raises(ValueError, match="16000"):
        load_clips(manifest(tmp_path, rate=8000), {"T5"})
    with pytest.raises(ValueError, match="scenario.json|run.json"):
        load_clips(tmp_path / "example", {"T5"})


def test_vap_samples_past_only_and_excludes_resumed_holds(tmp_path):
    (clip,) = load_clips(manifest(tmp_path), {"T5"})
    frames = [
        {"t": 1.59, "p_now": 0.2, "p_future": 0.3},
        {"t": 1.61, "p_now": 0.99, "p_future": 0.99},
        {"t": 2.6, "p_now": 0.4, "p_future": 0.5},
        {"t": 3.1, "p_now": 0.8, "p_future": 0.9},
    ]
    result = evaluate(clip, "vap", frames)
    assert result[0]["d0.1"] == [0.2, 0.3]
    summary = summarize({"vap": result})["vap"]
    point = next(
        r for r in summary if r["axis"] == "T5" and r["delay"] == 0.1 and r["score"] == "p_now"
    )
    assert point["shift"]["n"] == 1 and point["hold"]["n"] == 2
    assert point["auc"] == 1.0
    point = next(
        r for r in summary if r["axis"] == "T5" and r["delay"] == 0.2 and r["score"] == "p_now"
    )
    assert point["hold"]["n"] == 1  # gap == delay はすでに再開した時点


def test_vap_does_not_extrapolate_after_last_observed_frame(tmp_path):
    (clip,) = load_clips(manifest(tmp_path), {"T5"})
    result = evaluate(clip, "vap", [{"t": 3.0, "p_now": 0.8, "p_future": 0.9}])
    assert all(result[-1][f"d{delay}"] is None for delay in (0.1, 0.2, 0.3, 0.4))
    assert all(r["shift"]["n"] == 0 for r in summarize({"vap": result})["vap"])


def test_bc_assigns_disjoint_half_open_regions_and_reports_empty_categories(tmp_path):
    (clip,) = load_clips(manifest(tmp_path), {"T5"})
    times = [0.9, 1.1, 1.5, 1.9, 2.0, 2.5, 2.7, 3.0, 3.3, 3.4]
    rows = evaluate(clip, "bc", [{"t": t, "p_bc": 0.1 * i} for i, t in enumerate(times)])
    assert [r["region"] for r in rows] == [
        "speech",
        "pause_comma",
        "pause_comma",
        "speech",
        "pause_period",
        "speech",
        "end",
        "end",
    ]
    summary = summarize({"bc": rows})["bc"]
    by_region = {r["region"]: r for r in summary}
    assert by_region["pause_comma"]["distribution"]["n"] == 2
    assert by_region["pause_other"]["distribution"]["n"] == 0
    assert evaluate(clip, "bc_det", [{"t": 1.1, "p_bc_det": 0.9}]) == []


def test_bc_2type_keeps_reactive_and_emotional_scores_separate(tmp_path):
    (clip,) = load_clips(manifest(tmp_path), {"T5"})
    rows = evaluate(clip, "bc_2type", [{"t": 1.6, "p_bc_react": 0.8, "p_bc_emo": 0.2}])
    summary = summarize({"bc_2type": rows})["bc_2type"]
    scores = {r["score"] for r in summary}
    assert scores == {"p_bc_react", "p_bc_emo"}


def test_bc_detection_scores_last_utterance_once_not_each_frame(tmp_path):
    clips = load_clips(manifest(tmp_path, "T3"), {"T3"})
    assert evaluate(clips[0], "bc_det", [{"t": 1.1, "p_bc_det": 1.0}]) == []
    rows = evaluate(
        clips[1],
        "bc_det",
        [
            {"t": 0.9, "p_bc_det": 1.0},
            {"t": 1.0, "p_bc_det": 0.6},
            {"t": 1.2, "p_bc_det": 0.9},
            {"t": 1.6, "p_bc_det": 0.7},
            {"t": 1.7, "p_bc_det": 1.0},
        ],
    )
    assert len(rows) == 1
    assert rows[0]["kind"] == "backchannel"
    assert rows[0]["max"] == 0.9
    assert rows[0]["distribution"]["n"] == 3
    interrupt = dict(rows[0], id="interrupt", axis="T4", kind="interrupt", max=0.3)
    summary = summarize({"bc_det": rows + [interrupt]})["bc_det"]
    assert summary["auc"] == 1.0
    assert summary["backchannel"]["n"] == 1
    assert summary["interrupt"]["n"] == 1
    assert "maai" not in sys.modules
