"""MOCK: numeric callback replay, not a live guard or detector quality test."""

from __future__ import annotations

import copy
import json

import pytest

from scripts.diagnose_guard_history import analyze_case, summarize

pytestmark = pytest.mark.unit


def example(frames=(), *, axis="T3", bc=False, config=None):
    settings = {"barge_in": "True", "barge_guard": "True", "guard_bc_det": "0.25",
                "maai_stale_s": "0.25", "maai_vap_cpc": "False"}
    settings.update(config or {})
    run = {"private": False, "id": "synthetic-01", "axis": axis,
           "steps": [{"start": 0, "end": .5}, {"start": 1, "end": 1.8}],
           "events": [{"type": "text", "role": "user", "text": "never-export", "t": .1},
                      {"type": "audio", "generation": 1, "seq": 0, "n": 240000,
                       "played": True, "t": .2}]}
    trace = [{"event": "config", "t": 0, **settings},
             {"event": "stt_final", "t": .1, "text": "never-export"}]
    if bc:
        trace.append({"event": "backchannel", "generation": 1, "t": .19})
    trace += [{"event": "vad_start", "t": 1, "audio_s": 1, "at_s": 1}]
    trace += [{"event": "maai", "t": at + .01, "audio_s": at + .01,
               "at_s": at, "p_bc_det_user": value} for at, value in frames]
    trace += [{"event": "vad_sustained", "t": 1.512, "audio_s": 1.512, "at_s": 1.512},
              {"event": "stop", "t": 1.5121, "generation": 1},
              {"event": "vad_end", "t": 2, "audio_s": 2, "speech_end_s": 1.8}]
    trace.sort(key=lambda e: e["t"])
    run["events"].append({"type": "stop", "generation": 1, "t": 1.5121})
    return run, trace


def gate(result):
    assert len(result["gates"]) == 1
    return result["gates"][0]


def test_512ms_gate_separates_latest_recent_and_since_onset_without_retuning():
    run, trace = example([(1.032, .275), (1.112, .042), (1.272, .001), (1.432, 0)])
    result = analyze_case(run, trace)
    row = gate(result)
    assert row["voice_start_s"] == 1
    assert row["gate_at_s"] == 1.512
    assert row["decisions"]["current"]["decision"] == "stop"
    assert row["decisions"]["recent_250ms"]["decision"] == "stop"
    assert row["decisions"]["since_onset"]["decision"] == "hold"
    assert row["decisions"]["since_onset"]["value"] == .275
    assert row["decisions"]["since_onset"]["age_s"] == pytest.approx(.48)
    assert row["runtime_latest_known"] is False
    assert row["playing_evidence"] == "receiver_envelope_proxy"
    assert "never-export" not in json.dumps(result)


def test_low_detector_values_remain_stops_under_all_selectors():
    run, trace = example([(1.064, .033), (1.432, 0)])
    assert {x["decision"] for x in gate(analyze_case(run, trace))["decisions"].values()} == {"stop"}


def test_previous_voice_future_source_and_late_delivery_cannot_supply_peak():
    run, trace = example([(1.432, 0)])
    trace += [
        {"event": "maai", "t": 1.02, "audio_s": 1.02, "at_s": .96, "p_bc_det_user": .9},
        {"event": "maai", "t": 1.1, "audio_s": 1.6, "at_s": 1.6, "p_bc_det_user": .9},
        {"event": "maai", "t": 1.6, "audio_s": 1.6, "at_s": 1.1, "p_bc_det_user": .9},
    ]
    trace.sort(key=lambda e: e["t"])
    row = gate(analyze_case(run, trace))
    assert row["decisions"]["since_onset"]["decision"] == "stop"
    assert row["decisions"]["since_onset"]["value"] == 0


def test_stale_at_delivery_is_not_latched_and_missing_is_not_zero():
    run, trace = example()
    trace.insert(3, {"event": "maai", "t": 1.4, "audio_s": 1.4,
                     "at_s": 1.032, "p_bc_det_user": .9})
    trace.sort(key=lambda e: e["t"])
    row = gate(analyze_case(run, trace))
    assert row["decisions"]["since_onset"]["value"] is None
    assert row["decisions"]["since_onset"]["reason"] == "no_eligible_detector_frame"


def test_legacy_composite_freshness_is_separate_from_primary_history():
    run, trace = example([(1.432, .8)], config={"maai_vap_cpc": "True"})
    trace.append({"event": "maai", "t": .81, "audio_s": .81,
                  "at_s": .8, "p_now": [.5, .5]})
    trace.sort(key=lambda e: e["t"])
    row = gate(analyze_case(run, trace))
    assert row["decisions"]["current"]["reason"] == "stale_composite_frame"
    assert row["decisions"]["since_onset"]["decision"] == "hold"


@pytest.mark.parametrize("kind", ["bc_only", "not_playing", "guard_disabled", "barge_disabled"])
def test_generation_playing_and_configuration_nonfiring_reasons(kind):
    run, trace = example([(1.432, .9)], bc=kind == "bc_only")
    if kind == "not_playing":
        run["events"][1]["n"] = 240
    if kind.endswith("disabled"):
        trace[0]["barge_guard" if kind == "guard_disabled" else "barge_in"] = "False"
    row = gate(analyze_case(run, trace))
    assert all(x["decision"] == "not_eligible" for x in row["decisions"].values())
    assert all(x["reason"] for x in row["decisions"].values())


def test_short_voice_without_gate_and_private_rejection():
    run, trace = example()
    trace = [e for e in trace if e["event"] not in {"vad_sustained", "stop"}]
    result = analyze_case(run, trace)
    assert result["gates"] == [] and result["no_gate_reason"] == "no_sustained_gate"
    run["private"] = True
    with pytest.raises(ValueError, match="private"):
        analyze_case(run, trace)


def test_unknown_alignment_does_not_become_playing_false():
    run, trace = example([(1.432, .9)])
    run["events"] = [e for e in run["events"] if e["type"] != "text"]
    trace = [e for e in trace if e["event"] != "stt_final"]
    row = gate(analyze_case(run, trace))
    assert row["playing_at_gate"] is None
    assert row["decisions"]["current"]["decision"] == "unknown"


def test_t4_extra_hold_is_counterfactual_candidate_not_measured_harm():
    run, trace = example([(1.032, .9), (1.432, 0)], axis="T4")
    case = analyze_case(run, trace)
    report = summarize([case])
    assert report["t4_additional_hold_candidates"]["since_onset"] == 1
    assert report["t4_additional_hold_candidates"]["recent_250ms"] == 0
    assert report["measured_policy_improvement"] is False
    altered = copy.deepcopy(run)
    altered["events"][0]["text"] = "wrong-pair"
    with pytest.raises(ValueError, match="pair"):
        analyze_case(altered, trace)
