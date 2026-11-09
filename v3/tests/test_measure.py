import json

import pytest

from tomoko_v3.measure import LatencyRecorder, percentile, summarize

pytestmark = pytest.mark.unit


def test_percentile_nearest_rank():
    values = list(range(1, 101))  # 1..100
    assert percentile(values, 50) == 50
    assert percentile(values, 95) == 95
    assert percentile(values, 99) == 99
    assert percentile(values, 100) == 100
    assert percentile([7.0], 99) == 7.0


def test_percentile_rejects_empty():
    with pytest.raises(ValueError):
        percentile([], 50)


def test_summarize_counts_threshold_and_runs():
    # 超過は 3 回、最長の連続超過は 2 回
    samples = [10.0, 90.0, 95.0, 20.0, 100.0, 30.0]
    s = summarize(samples, threshold_ms=80.0)
    assert s["count"] == 6
    assert s["max_ms"] == 100.0
    assert s["min_ms"] == 10.0
    assert s["over_threshold"] == 3
    assert s["over_threshold_ratio"] == pytest.approx(0.5)
    assert s["max_consecutive_over"] == 2
    assert s["runs_of_3_or_more_over"] == 0


def test_summarize_counts_long_runs():
    samples = [90.0] * 3 + [10.0] + [90.0] * 4
    s = summarize(samples, threshold_ms=80.0)
    assert s["max_consecutive_over"] == 4
    assert s["runs_of_3_or_more_over"] == 2


def test_summarize_empty_is_explicit():
    s = summarize([], threshold_ms=80.0)
    assert s["count"] == 0
    assert s["p50_ms"] is None


def test_recorder_keeps_failures_in_denominator(tmp_path):
    rec = LatencyRecorder(
        name="demo",
        start_point="text handed to TTS",
        end_point="first audio sample received",
        threshold_ms=250.0,
        conditions={"warm": True},
    )
    rec.add(100.0)
    rec.add(300.0)
    rec.add_failure("no audio")
    out = rec.to_dict()
    assert out["attempts"] == 3
    assert out["failures"] == 1
    assert out["failure_reasons"] == {"no audio": 1}
    assert out["summary"]["count"] == 2
    assert out["summary"]["over_threshold"] == 1
    assert out["start_point"] == "text handed to TTS"
    assert out["started_at"] is not None and out["finished_at"] is not None

    path = rec.write_json(tmp_path / "x.json")
    loaded = json.loads(path.read_text())
    assert loaded["samples_ms"] == [100.0, 300.0]


def test_recorder_time_context_records_elapsed():
    clock = iter([1_000_000_000, 1_050_000_000])  # 50 ms
    rec = LatencyRecorder(name="t", start_point="a", end_point="b", clock_ns=lambda: next(clock))
    with rec.time():
        pass
    assert rec.samples_ms == [pytest.approx(50.0)]


def test_recorder_time_context_records_failure_on_exception():
    rec = LatencyRecorder(name="t", start_point="a", end_point="b")
    with pytest.raises(RuntimeError):
        with rec.time():
            raise RuntimeError("boom")
    assert rec.samples_ms == []
    assert rec.failures == 1
