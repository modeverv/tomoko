from __future__ import annotations

import json
from pathlib import Path

import pytest

from _tools.system_metrics import (
    SystemMetricsSample,
    collect_mactop_sample,
    default_mactop_timeout_sec,
    latest_system_metrics_sample,
    normalize_mactop_sample,
    parse_mactop_headless_output,
)


@pytest.mark.unit
def test_parse_mactop_headless_output_normalizes_gpu_pressure() -> None:
    output = json.dumps(
        [
            {
                "timestamp": "2026-05-31T12:00:00+09:00",
                "soc_metrics": {
                    "GPUActive": 73.5,
                    "GPUPower": 5.2,
                    "GPUSRAMPower": 0.4,
                    "GPUFreqMHz": 900,
                    "ANEPower": 0.7,
                    "CPUPower": 4.1,
                    "DRAMPower": 1.3,
                    "TotalPower": 14.2,
                },
                "memory": {
                    "total": 68719476736,
                    "used": 34359738368,
                    "available": 34359738368,
                    "swap_total": 8589934592,
                    "swap_used": 1073741824,
                },
                "system_info": {"name": "Apple M3 Max", "gpu_core_count": 40},
                "thermal_state": "Nominal",
                "gpu_temp": 58.25,
            }
        ]
    )

    sample = parse_mactop_headless_output(output)

    assert sample.available is True
    assert sample.provider == "mactop"
    assert sample.gpu_active_percent == 73.5
    assert sample.gpu_power_w == 5.2
    assert sample.gpu_sram_power_w == 0.4
    assert sample.gpu_total_power_w == 5.6
    assert sample.gpu_freq_mhz == 900
    assert sample.ane_power_w == 0.7
    assert sample.memory_used_bytes == 34359738368
    assert sample.system_name == "Apple M3 Max"
    assert sample.thermal_state == "Nominal"


@pytest.mark.unit
def test_normalize_mactop_sample_accepts_json_tags_and_snake_case() -> None:
    sample = normalize_mactop_sample(
        {
            "timestamp": "2026-05-31T12:00:00+09:00",
            "soc_metrics": {
                "gpu_active": 10.0,
                "gpu_power": 1.2,
                "gpu_sram_power": 0.3,
                "gpu_freq_mhz": 500,
            },
            "memory": {"total": 10, "used": 4, "available": 6},
        }
    )

    assert sample.gpu_active_percent == 10.0
    assert sample.gpu_power_w == 1.2
    assert sample.gpu_total_power_w == 1.5
    assert sample.gpu_freq_mhz == 500


@pytest.mark.unit
def test_latest_system_metrics_sample_reads_last_valid_json_line(tmp_path: Path) -> None:
    log_path = tmp_path / "system-metrics.jsonl"
    first = SystemMetricsSample.unavailable(provider="mactop", error="missing")
    second = SystemMetricsSample(
        provider="mactop",
        available=True,
        timestamp="2026-05-31T12:00:00+09:00",
        gpu_active_percent=42.0,
    )
    log_path.write_text(
        json.dumps(first.to_json()) + "\nnot json\n" + json.dumps(second.to_json()) + "\n",
        encoding="utf-8",
    )

    latest = latest_system_metrics_sample(log_path)

    assert latest is not None
    assert latest.available is True
    assert latest.gpu_active_percent == 42.0


@pytest.mark.unit
def test_latest_system_metrics_sample_prefers_recent_available_sample(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "system-metrics.jsonl"
    available = SystemMetricsSample(
        provider="mactop",
        available=True,
        timestamp="2026-05-31T12:00:00+09:00",
        gpu_active_percent=42.0,
    )
    unavailable = SystemMetricsSample.unavailable(provider="mactop", error="timeout")
    log_path.write_text(
        json.dumps(available.to_json()) + "\n" + json.dumps(unavailable.to_json()) + "\n",
        encoding="utf-8",
    )

    latest = latest_system_metrics_sample(log_path)

    assert latest is not None
    assert latest.available is True
    assert latest.gpu_active_percent == 42.0


@pytest.mark.unit
def test_default_mactop_timeout_scales_with_interval() -> None:
    assert default_mactop_timeout_sec(interval_sec=2.0) >= 10.0
    assert default_mactop_timeout_sec(interval_sec=10.0) >= 18.0


@pytest.mark.unit
def test_collect_mactop_sample_uses_scaled_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, float] = {}

    def fake_run(*args: object, **kwargs: object) -> object:
        del args
        captured["timeout"] = float(kwargs["timeout"])

        class Result:
            returncode = 0
            stdout = json.dumps(
                [
                    {
                        "timestamp": "2026-05-31T12:00:00+09:00",
                        "soc_metrics": {"GPUActive": 1.0},
                    }
                ]
            )
            stderr = ""

        return Result()

    monkeypatch.setattr("subprocess.run", fake_run)

    sample = collect_mactop_sample(command="mactop", interval_ms=2000)

    assert sample.available is True
    assert captured["timeout"] >= 10.0
