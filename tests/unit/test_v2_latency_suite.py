from __future__ import annotations

import pytest

from scripts.v2_latency_suite import (
    RunResult,
    classify_run,
    evaluate_targets,
    percentile,
    select_seeds,
    summarize,
)

pytestmark = pytest.mark.unit


def test_percentile() -> None:
    values = [100.0, 200.0, 300.0, 400.0, 500.0]
    assert percentile(values, 50) == 300.0
    assert percentile(values, 95) == pytest.approx(480.0)
    assert percentile([42.0], 95) == 42.0
    assert percentile([], 50) is None


def test_select_seeds_filters_by_category_and_count() -> None:
    lines = [
        "# --- request: 応答要求 ---",
        "今日の予定を教えて",
        "今何時か教えて",
        "# --- chat: 雑談 ---",
        "最近疲れてるんだよね",
        "",
    ]
    request_seeds = select_seeds(lines, count=1, category="request")
    all_seeds = select_seeds(lines, count=10, category=None)

    assert request_seeds == ["今日の予定を教えて"]
    assert all_seeds == ["今日の予定を教えて", "今何時か教えて", "最近疲れてるんだよね"]


def _timeline_partial_origin() -> list[dict]:
    return [
        {"elapsed_ms": 100.0, "type": "transcript", "payload": {"is_final": False}},
        {
            "elapsed_ms": 200.0,
            "type": "speech_order",
            "payload": {"mode": "replace_current"},
        },
        {"elapsed_ms": 300.0, "type": "binary_audio", "bytes": 1000},
        {"elapsed_ms": 400.0, "type": "transcript", "payload": {"is_final": True}},
        {
            "elapsed_ms": 450.0,
            "type": "scheduler_decision",
            "payload": {"reason": "final reconciled with active partial reply"},
        },
    ]


def test_classify_run_partial_origin_and_reconcile() -> None:
    run = classify_run(_timeline_partial_origin(), voice_end_elapsed_ms=250.0)
    assert run["partial_origin"] is True
    assert run["reconciled"] is True
    assert run["false_early"] is False
    assert run["first_audio_ms"] == pytest.approx(50.0)


def test_classify_run_allows_audio_before_voice_end() -> None:
    timeline = [
        {"elapsed_ms": 100.0, "type": "transcript", "payload": {"is_final": False}},
        {
            "elapsed_ms": 200.0,
            "type": "speech_order",
            "payload": {"mode": "replace_current"},
        },
        {"elapsed_ms": 300.0, "type": "binary_audio", "bytes": 1000},
        {"elapsed_ms": 650.0, "type": "transcript", "payload": {"is_final": True}},
    ]
    run = classify_run(timeline, voice_end_elapsed_ms=500.0)
    assert run["partial_origin"] is True
    assert run["first_audio_ms"] == pytest.approx(-200.0)


def test_classify_run_final_origin() -> None:
    timeline = [
        {"elapsed_ms": 400.0, "type": "transcript", "payload": {"is_final": True}},
        {
            "elapsed_ms": 500.0,
            "type": "speech_order",
            "payload": {"mode": "replace_current"},
        },
        {"elapsed_ms": 900.0, "type": "binary_audio", "bytes": 1000},
    ]
    run = classify_run(timeline, voice_end_elapsed_ms=300.0)
    assert run["partial_origin"] is False
    assert run["reconciled"] is False
    assert run["false_early"] is False
    assert run["first_audio_ms"] == pytest.approx(600.0)
    assert run["first_final_elapsed_ms"] == pytest.approx(400.0)
    assert run["first_order_elapsed_ms"] == pytest.approx(500.0)
    assert run["first_audio_elapsed_ms"] == pytest.approx(900.0)
    assert run["voice_end_to_final_ms"] == pytest.approx(100.0)
    assert run["voice_end_to_order_ms"] == pytest.approx(200.0)
    assert run["order_to_first_audio_ms"] == pytest.approx(400.0)
    assert run["final_to_first_audio_ms"] == pytest.approx(500.0)


def test_classify_run_keeps_latency_stage_breakdown() -> None:
    timeline = [
        {
            "elapsed_ms": 120.0,
            "type": "latency_stage",
            "payload": {
                "origin": "partial",
                "stage_timings_ms": {
                    "stt_partial_ms": 20.0,
                    "tomoko_ms": 30.0,
                    "tts_ms": 0.0,
                    "total_ms": 50.0,
                },
            },
        },
        {
            "elapsed_ms": 130.0,
            "type": "scheduler_decision",
            "payload": {"reason": "partial incomplete"},
        },
        {
            "elapsed_ms": 390.0,
            "type": "latency_stage",
            "payload": {
                "origin": "final",
                "stage_timings_ms": {
                    "stt_ms": 120.0,
                    "tomoko_ms": 340.0,
                    "tts_ms": 80.0,
                    "total_ms": 540.0,
                },
            },
        },
        {"elapsed_ms": 400.0, "type": "transcript", "payload": {"is_final": True}},
        {
            "elapsed_ms": 500.0,
            "type": "speech_order",
            "payload": {"mode": "replace_current"},
        },
        {"elapsed_ms": 900.0, "type": "binary_audio", "bytes": 1000},
    ]
    run = classify_run(timeline, voice_end_elapsed_ms=300.0)
    assert run["stage_timings_ms"]["stt_ms"] == pytest.approx(120.0)
    assert run["stage_timings_ms"]["tomoko_ms"] == pytest.approx(340.0)
    assert run["stage_timings_ms"]["tts_ms"] == pytest.approx(80.0)
    assert run["stage_timings_ms"]["total_ms"] == pytest.approx(540.0)


def test_classify_run_false_early_from_divergence_reason() -> None:
    timeline = [
        {"elapsed_ms": 100.0, "type": "transcript", "payload": {"is_final": False}},
        {
            "elapsed_ms": 150.0,
            "type": "speech_order",
            "payload": {"mode": "replace_current", "reason": "partial_speech_order"},
        },
        {"elapsed_ms": 200.0, "type": "binary_audio", "bytes": 1000},
        {"elapsed_ms": 500.0, "type": "transcript", "payload": {"is_final": True}},
        {
            "elapsed_ms": 530.0,
            "type": "speech_order",
            "payload": {
                "mode": "replace_current",
                "reason": "final diverged from active partial reply; replacing",
            },
        },
    ]
    run = classify_run(timeline, voice_end_elapsed_ms=300.0)
    assert run["partial_origin"] is True
    assert run["false_early"] is True


def test_summarize_reports_rates() -> None:
    stats = summarize(
        [
            RunResult(
                seed="a",
                repeat=0,
                first_audio_ms=700.0,
                partial_origin=True,
                reconciled=True,
                false_early=False,
                final_transcript="",
                stage_timings_ms={"tomoko_ms": 300.0, "tts_ms": 500.0},
            ),
            RunResult(
                seed="b",
                repeat=0,
                first_audio_ms=1600.0,
                partial_origin=False,
                reconciled=False,
                false_early=True,
                final_transcript="",
                stage_timings_ms={"tomoko_ms": 700.0, "tts_ms": 900.0},
            ),
        ]
    )
    assert stats["partial_origin_rate"] == pytest.approx(0.5)
    assert stats["reconcile_rate"] == pytest.approx(0.5)
    assert stats["false_early_rate"] == pytest.approx(0.5)
    assert stats["stage_tomoko_p50_ms"] == pytest.approx(500.0)
    assert stats["stage_tts_p95_ms"] == pytest.approx(880.0)


def test_evaluate_targets() -> None:
    stats = {
        "runs_no_audio": 0,
        "final_origin_p50_ms": 1200.0,
        "final_origin_p95_ms": 2600.0,
        "partial_origin_p50_ms": 700.0,
    }
    targets = {
        "final_origin_p50_ms": 1500.0,
        "final_origin_p95_ms": 2500.0,
        "partial_origin_p50_ms": 800.0,
    }
    results = evaluate_targets(stats, targets)
    by_name = {result.name: result.ok for result in results}
    assert by_name["final_origin_p50_ms<=1500.0"] is True
    assert by_name["final_origin_p95_ms<=2500.0"] is False
    assert by_name["partial_origin_p50_ms<=800.0"] is True
    assert by_name["runs_no_audio==0"] is True


def test_evaluate_targets_fails_when_any_run_has_no_audio() -> None:
    results = evaluate_targets(
        {
            "runs_no_audio": 1,
            "final_origin_p50_ms": None,
            "final_origin_p95_ms": None,
            "partial_origin_p50_ms": None,
        },
        {
            "final_origin_p50_ms": 1500.0,
            "final_origin_p95_ms": 2500.0,
            "partial_origin_p50_ms": 800.0,
        },
    )
    by_name = {result.name: result.ok for result in results}
    assert by_name["runs_no_audio==0"] is False
