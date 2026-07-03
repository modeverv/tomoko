from __future__ import annotations

import json

import pytest

from scripts.v2_scenario_replay import (
    Scenario,
    ScenarioStep,
    apply_cut_ratio,
    evaluate_expectations,
    is_subsequence,
    load_scenario,
    scenario_control_events,
)

pytestmark = pytest.mark.unit


def _write_scenario(tmp_path, payload):
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_scenario_fills_defaults(tmp_path) -> None:
    path = _write_scenario(
        tmp_path,
        {
            "name": "basic",
            "steps": [
                {"say": "トモコ、返事して"},
                {"event": {"type": "initiative_tick"}, "wait_for": "speech_order"},
            ],
            "fake_personality": {"talkativeness": 0.9},
            "fake_user_status": {"present": True, "activity_label": "coding"},
            "fake_candidates": [{"source": "world", "source_key": "rain"}],
            "fake_world_info": [{"source_key": "rain", "text": "雨", "confidence": 0.9}],
            "expect": {"min_events": {"speech_order": 1}},
        },
    )
    scenario = load_scenario(path)
    assert isinstance(scenario, Scenario)
    assert scenario.name == "basic"
    assert scenario.runtime == "any"
    assert len(scenario.steps) == 2
    step = scenario.steps[0]
    assert isinstance(step, ScenarioStep)
    assert step.say == "トモコ、返事して"
    assert step.event is None
    assert step.cut_at_ratio is None
    assert step.overlap_during_playback is False
    assert step.wait_for == "prompt_complete"
    assert scenario.steps[1].say == ""
    assert scenario.steps[1].event == {"type": "initiative_tick"}
    assert scenario.steps[1].wait_for == "speech_order"
    assert scenario.expect.min_events == {"speech_order": 1}
    assert scenario.expect.max_events == {}
    assert scenario.expect.forbid_events == []
    assert scenario.fake_personality == {"talkativeness": 0.9}
    assert scenario.fake_user_status == {"present": True, "activity_label": "coding"}
    assert scenario.fake_candidates == [{"source": "world", "source_key": "rain"}]
    assert scenario.fake_world_info == [{"source_key": "rain", "text": "雨", "confidence": 0.9}]


def test_scenario_control_events_include_calendar_fixture_for_real_runtime() -> None:
    scenario = Scenario(
        name="calendar",
        fake_calendar=[{"offset_min": 5, "title": "定例会議"}],
        steps=[ScenarioStep(say="トモコ、今どう")],
    )

    assert scenario_control_events(scenario) == [
        {"type": "latency_control", "command": "reset_conversation"},
        {
            "type": "latency_control",
            "command": "set_fake_calendar",
            "items": [{"offset_min": 5, "title": "定例会議"}],
        }
    ]


def test_load_scenario_rejects_missing_steps(tmp_path) -> None:
    path = _write_scenario(tmp_path, {"name": "broken", "expect": {}})
    with pytest.raises(ValueError):
        load_scenario(path)


def test_apply_cut_ratio_truncates_samples() -> None:
    samples = tuple(float(index) for index in range(1000))
    cut = apply_cut_ratio(samples, 0.6)
    assert len(cut) == 600
    assert cut[0] == 0.0
    assert apply_cut_ratio(samples, None) == samples
    with pytest.raises(ValueError):
        apply_cut_ratio(samples, 1.5)


def test_is_subsequence() -> None:
    haystack = ["ready", "transcript", "scheduler_decision", "speech_order", "binary_audio"]
    assert is_subsequence(["transcript", "speech_order"], haystack)
    assert is_subsequence(["speech_order", "binary_audio"], haystack)
    assert not is_subsequence(["speech_order", "transcript"], haystack)


def _artifact() -> dict:
    return {
        "event_counts": {
            "transcript": 2,
            "scheduler_decision": 1,
            "speech_order": 1,
            "binary_audio": 1,
            "prompt_complete": 1,
        },
        "timeline": [
            {"elapsed_ms": 1.0, "type": "transcript", "payload": {"is_final": False}},
            {
                "elapsed_ms": 5.0,
                "type": "transcript",
                "payload": {"is_final": True, "text": "トモコ、返事して"},
            },
            {
                "elapsed_ms": 6.0,
                "type": "scheduler_decision",
                "payload": {
                    "action": "replace_current",
                    "score_breakdown": {
                        "pressure_dialogue_yielding_opportunity": 0.92,
                        "pressure_dialogue_silence_opportunity": 0.1,
                    },
                },
            },
            {
                "elapsed_ms": 7.0,
                "type": "speech_order",
                "payload": {
                    "mode": "replace_current",
                    "text": "うん",
                    "reason": "unit reason",
                },
            },
            {"elapsed_ms": 9.0, "type": "binary_audio", "bytes": 16},
            {"elapsed_ms": 10.0, "type": "prompt_complete", "payload": {}},
        ],
        "steps": [
            {"say": "トモコ、返事して", "voice_end_to_first_audio_ms": 900.0},
        ],
    }


def test_evaluate_expectations_pass() -> None:
    expect = {
        "min_events": {"speech_order": 1, "binary_audio": 1},
        "max_events": {"speech_order": 1},
        "require_event_order": ["transcript", "speech_order", "binary_audio"],
        "forbid_events": ["prompt_error"],
        "max_voice_end_to_first_audio_ms": 2000.0,
        "final_transcript_contains": "返事",
        "speech_order_modes": ["replace_current"],
        "speech_order_text_contains": "うん",
        "speech_order_reason_contains": "unit",
        "max_speech_order_text_chars": 3,
        "score_breakdown_any": {
            "pressure_dialogue_yielding_opportunity": {"min": 0.9},
            "pressure_dialogue_silence_opportunity": {"max": 0.2},
        },
    }
    results = evaluate_expectations(expect, _artifact())
    assert results
    assert all(result.ok for result in results)


def test_evaluate_expectations_failures() -> None:
    artifact = _artifact()
    expect = {
        "min_events": {"speech_order": 2},
        "forbid_events": ["scheduler_decision"],
        "max_voice_end_to_first_audio_ms": 100.0,
        "speech_order_modes": ["stop"],
    }
    results = evaluate_expectations(expect, artifact)
    failed = [result for result in results if not result.ok]
    assert len(failed) == 4


def test_evaluate_expectations_score_breakdown_any_failure() -> None:
    expect = {
        "score_breakdown_any": {
            "pressure_dialogue_yielding_opportunity": {"min": 0.95},
        },
    }

    results = evaluate_expectations(expect, _artifact())

    assert len(results) == 1
    assert results[0].ok is False
    assert "pressure_dialogue_yielding_opportunity" in results[0].detail
