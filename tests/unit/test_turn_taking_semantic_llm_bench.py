from __future__ import annotations

import pytest

from _tools.bench_turn_taking_semantic_llm import (
    SemanticCase,
    extract_json_object,
    parse_semantic_json,
    score_case,
    summarize,
)


@pytest.mark.unit
def test_extract_json_object_accepts_plain_and_fenced_json() -> None:
    assert extract_json_object('{"semantic_saturation": 0.9}') == (
        '{"semantic_saturation": 0.9}'
    )
    assert extract_json_object(
        '```json\n{"semantic_saturation": 0.9, "remaining_info_risk": 0.1}\n```'
    ) == '{"semantic_saturation": 0.9, "remaining_info_risk": 0.1}'


@pytest.mark.unit
def test_parse_semantic_json_validates_required_numeric_range() -> None:
    parsed = parse_semantic_json(
        '{"semantic_saturation": 0.8, "remaining_info_risk": 0.2}'
    )

    assert parsed["parse_ok"] is True
    assert parsed["shape_ok"] is True
    assert parsed["range_ok"] is True
    assert parsed["semantic_saturation"] == 0.8

    extra_key = parse_semantic_json(
        '{"semantic_saturation": 0.8, "remaining_info_risk": 0.2, '
        '"explanation": "done"}'
    )
    assert extra_key["parse_ok"] is True
    assert extra_key["shape_ok"] is False

    out_of_range = parse_semantic_json(
        '{"semantic_saturation": 1.8, "remaining_info_risk": 0.2}'
    )
    assert out_of_range["parse_ok"] is True
    assert out_of_range["range_ok"] is False

    invalid = parse_semantic_json("説明だけ")
    assert invalid["parse_ok"] is False
    assert invalid["parse_error"] == "json_object_not_found"


@pytest.mark.unit
def test_score_case_uses_saturation_and_remaining_risk_thresholds() -> None:
    case = SemanticCase(
        case_id="complete",
        text="終わり。",
        expected_saturation=0.9,
        expected_remaining_risk=0.1,
        expected_finished=True,
        category="complete",
    )

    score = score_case(
        case,
        {
            "parse_ok": True,
            "shape_ok": True,
            "range_ok": True,
            "semantic_saturation": 0.8,
            "remaining_info_risk": 0.3,
        },
    )

    assert score["predicted_finished"] is True
    assert score["finished_correct"] is True
    assert score["saturation_abs_error"] == pytest.approx(0.1)


@pytest.mark.unit
def test_summarize_reports_parse_accuracy_latency_and_confusion() -> None:
    rows = [
        {
            "error": None,
            "parse_ok": True,
            "shape_ok": True,
            "range_ok": True,
            "case": {"expected_finished": True},
            "predicted_finished": True,
            "finished_correct": True,
            "saturation_abs_error": 0.1,
            "remaining_risk_abs_error": 0.1,
            "first_delta_ms": 10.0,
            "total_ms": 20.0,
        },
        {
            "error": None,
            "parse_ok": True,
            "shape_ok": True,
            "range_ok": True,
            "case": {"expected_finished": False},
            "predicted_finished": True,
            "finished_correct": False,
            "saturation_abs_error": 0.4,
            "remaining_risk_abs_error": 0.3,
            "first_delta_ms": 30.0,
            "total_ms": 50.0,
        },
    ]

    summary = summarize(rows)

    assert summary["parse_ok_rate"] == 1.0
    assert summary["shape_ok_rate"] == 1.0
    assert summary["finished_accuracy"] == 0.5
    assert summary["saturation_mae"] == pytest.approx(0.25)
    assert summary["avg_first_delta_ms"] == pytest.approx(20.0)
    assert summary["confusion"] == {"tp": 1, "fp": 1, "tn": 0, "fn": 0}
