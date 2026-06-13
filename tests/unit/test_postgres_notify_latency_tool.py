from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from _tools.bench_postgres_notify_latency import (
    NotifySample,
    percentile,
    summarize_samples,
    validate_channel,
    write_json_result,
)


@pytest.mark.unit
def test_percentile_interpolates_sorted_values() -> None:
    assert percentile([10.0, 20.0, 30.0, 40.0], 50) == 25.0
    assert percentile([10.0, 20.0, 30.0, 40.0], 95) == pytest.approx(38.5)


@pytest.mark.unit
def test_summarize_samples_reports_distribution_and_missing_count() -> None:
    summary = summarize_samples(
        [
            NotifySample(
                sequence=0,
                latency_ms=0.4,
                notify_execute_ms=0.3,
                receive_lag_after_execute_ms=0.1,
            ),
            NotifySample(
                sequence=1,
                latency_ms=0.6,
                notify_execute_ms=0.4,
                receive_lag_after_execute_ms=0.2,
            ),
            NotifySample(
                sequence=2,
                latency_ms=1.1,
                notify_execute_ms=0.5,
                receive_lag_after_execute_ms=0.6,
            ),
        ],
        dropped_warmup=2,
        expected_samples=4,
    )

    assert summary.samples == 3
    assert summary.dropped_warmup == 2
    assert summary.missing == 1
    assert summary.avg_ms == pytest.approx(0.7)
    assert summary.p50_ms == 0.6
    assert summary.p95_ms == pytest.approx(1.05)
    assert summary.notify_execute_avg_ms == pytest.approx(0.4)


@pytest.mark.unit
def test_validate_channel_rejects_unsafe_identifier() -> None:
    assert validate_channel("tomoko_notify_latency_bench") == "tomoko_notify_latency_bench"
    with pytest.raises(argparse.ArgumentTypeError):
        validate_channel("bad-channel;notify")


@pytest.mark.unit
def test_write_json_result_preserves_sample_rows(tmp_path: Path) -> None:
    output_path = tmp_path / "notify.json"
    samples = [
        NotifySample(
            sequence=0,
            latency_ms=0.4,
            notify_execute_ms=0.3,
            receive_lag_after_execute_ms=0.1,
        )
    ]
    summary = summarize_samples(samples, dropped_warmup=1, expected_samples=1)

    write_json_result(
        output_path,
        config_path=Path("config/central_realtime.toml"),
        channel="tomoko_notify_latency_bench",
        summary=summary,
        samples=samples,
    )

    text = output_path.read_text()
    assert '"channel": "tomoko_notify_latency_bench"' in text
    assert '"latency_ms": 0.4' in text
