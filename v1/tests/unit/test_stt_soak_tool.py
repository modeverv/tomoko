from __future__ import annotations

import pytest

from _tools.soak_stt_backends import RunningStats, SoakStats, percentile, summary_payload


@pytest.mark.unit
def test_percentile_returns_nearest_rank_for_recent_values() -> None:
    assert percentile([], 95) == 0.0
    assert percentile([100.0], 95) == 100.0
    assert percentile([100.0, 120.0, 140.0, 160.0, 180.0], 95) == 180.0


@pytest.mark.unit
def test_running_stats_tracks_count_avg_min_max() -> None:
    stats = RunningStats()

    stats.add(100.0)
    stats.add(140.0)
    stats.add(120.0)

    assert stats.count == 3
    assert stats.avg_ms == 120.0
    assert stats.min_ms == 100.0
    assert stats.max_ms == 140.0


@pytest.mark.unit
def test_soak_stats_keeps_recent_window_and_summary_payload() -> None:
    stats = SoakStats(
        backend="local_whisper_mlx_small",
        type="mlx_whisper",
        model="mlx-community/whisper-small-mlx",
        command=None,
        streaming=True,
        started_at="2026-05-25T00:00:00+00:00",
        stats=RunningStats(),
        recent_ms=[],
    )

    stats.add_run(100.0, "one", recent_limit=2)
    stats.add_run(120.0, "two", recent_limit=2)
    stats.add_run(140.0, "three", recent_limit=2)
    payload = summary_payload(stats, elapsed_sec=10.0)

    assert stats.recent_ms == [120.0, 140.0]
    assert stats.last_text == "three"
    assert payload["count"] == 3
    assert payload["qps"] == 0.3
    assert payload["recent_p95_ms"] == 140.0
