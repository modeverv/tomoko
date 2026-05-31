from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class SystemMetricsSample:
    provider: str
    available: bool
    timestamp: str | None = None
    gpu_active_percent: float | None = None
    gpu_power_w: float | None = None
    gpu_sram_power_w: float | None = None
    gpu_total_power_w: float | None = None
    gpu_freq_mhz: float | None = None
    gpu_temp_c: float | None = None
    ane_power_w: float | None = None
    cpu_power_w: float | None = None
    dram_power_w: float | None = None
    total_power_w: float | None = None
    memory_total_bytes: int | None = None
    memory_used_bytes: int | None = None
    memory_available_bytes: int | None = None
    swap_total_bytes: int | None = None
    swap_used_bytes: int | None = None
    system_name: str | None = None
    gpu_core_count: int | None = None
    thermal_state: str | None = None
    error: str | None = None

    @classmethod
    def unavailable(cls, *, provider: str, error: str) -> SystemMetricsSample:
        return cls(
            provider=provider,
            available=False,
            timestamp=datetime.now(UTC).isoformat(),
            error=error,
        )

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def collect_mactop_sample(
    *,
    command: str = "mactop",
    interval_ms: int = 1000,
    timeout_sec: float | None = None,
) -> SystemMetricsSample:
    resolved_timeout_sec = (
        timeout_sec
        if timeout_sec is not None
        else default_mactop_timeout_sec(interval_sec=interval_ms / 1000)
    )
    try:
        result = subprocess.run(
            [
                command,
                "--headless",
                "--count",
                "1",
                "--interval",
                str(interval_ms),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=resolved_timeout_sec,
        )
    except FileNotFoundError:
        return SystemMetricsSample.unavailable(
            provider="mactop",
            error=f"command_not_found:{command}",
        )
    except subprocess.TimeoutExpired:
        return SystemMetricsSample.unavailable(
            provider="mactop",
            error=f"timeout_after_sec:{resolved_timeout_sec}",
        )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit:{result.returncode}"
        return SystemMetricsSample.unavailable(provider="mactop", error=detail)
    try:
        return parse_mactop_headless_output(result.stdout)
    except ValueError as exc:
        return SystemMetricsSample.unavailable(provider="mactop", error=str(exc))


def parse_mactop_headless_output(output: str) -> SystemMetricsSample:
    text = output.strip()
    if not text:
        raise ValueError("empty_mactop_output")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        objects = []
        for line in text.splitlines():
            line = line.strip().strip(",")
            if not line or line in {"[", "]"}:
                continue
            try:
                objects.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        payload = objects
    if isinstance(payload, list):
        payload = next((item for item in reversed(payload) if isinstance(item, dict)), None)
    if not isinstance(payload, dict):
        raise ValueError("mactop_output_has_no_sample")
    return normalize_mactop_sample(payload)


def default_mactop_timeout_sec(*, interval_sec: float) -> float:
    return max(10.0, interval_sec + 8.0)


def normalize_mactop_sample(payload: dict[str, Any]) -> SystemMetricsSample:
    soc = _dict(payload.get("soc_metrics"))
    memory = _dict(payload.get("memory"))
    system_info = _dict(payload.get("system_info"))
    gpu_power = _float(_get(soc, "GPUPower", "gpu_power"))
    gpu_sram_power = _float(_get(soc, "GPUSRAMPower", "gpu_sram_power"))
    return SystemMetricsSample(
        provider="mactop",
        available=True,
        timestamp=_str(payload.get("timestamp")) or datetime.now(UTC).isoformat(),
        gpu_active_percent=_float(
            _get(soc, "GPUActive", "gpu_active", default=payload.get("gpu_usage"))
        ),
        gpu_power_w=gpu_power,
        gpu_sram_power_w=gpu_sram_power,
        gpu_total_power_w=_sum_optional(gpu_power, gpu_sram_power),
        gpu_freq_mhz=_float(_get(soc, "GPUFreqMHz", "gpu_freq_mhz")),
        gpu_temp_c=_float(_get(soc, "GPUTemp", "gpu_temp", default=payload.get("gpu_temp"))),
        ane_power_w=_float(_get(soc, "ANEPower", "ane_power")),
        cpu_power_w=_float(_get(soc, "CPUPower", "cpu_power")),
        dram_power_w=_float(_get(soc, "DRAMPower", "dram_power")),
        total_power_w=_float(_get(soc, "TotalPower", "total_power")),
        memory_total_bytes=_int(memory.get("total")),
        memory_used_bytes=_int(memory.get("used")),
        memory_available_bytes=_int(memory.get("available")),
        swap_total_bytes=_int(memory.get("swap_total")),
        swap_used_bytes=_int(memory.get("swap_used")),
        system_name=_str(system_info.get("name")),
        gpu_core_count=_int(system_info.get("gpu_core_count")),
        thermal_state=_str(payload.get("thermal_state")),
    )


def latest_system_metrics_sample(path: Path) -> SystemMetricsSample | None:
    if not path.exists():
        return None
    latest_unavailable: SystemMetricsSample | None = None
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            sample = SystemMetricsSample(**payload)
            if sample.available:
                return sample
            if latest_unavailable is None:
                latest_unavailable = sample
    return latest_unavailable


def run_sampler(
    *,
    provider: str,
    command: str,
    output_path: Path,
    interval_sec: float,
    count: int,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    interval_ms = max(100, int(interval_sec * 1000))
    samples = 0
    with output_path.open("a", encoding="utf-8") as fp:
        while count <= 0 or samples < count:
            if provider != "mactop":
                sample = SystemMetricsSample.unavailable(
                    provider=provider,
                    error=f"unsupported_provider:{provider}",
                )
            else:
                sample = collect_mactop_sample(
                    command=command,
                    interval_ms=interval_ms,
                )
            fp.write(json.dumps(sample.to_json(), ensure_ascii=False) + "\n")
            fp.flush()
            print(_format_sample(sample), flush=True)
            samples += 1
            if count > 0 and samples >= count:
                break
            time.sleep(interval_sec)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sample Apple Silicon GPU pressure into Tomoko JSONL logs.",
    )
    parser.add_argument("--provider", default="mactop")
    parser.add_argument("--command", default="mactop")
    parser.add_argument("--output", type=Path, default=Path("logs/system-metrics.jsonl"))
    parser.add_argument("--interval-sec", type=float, default=2.0)
    parser.add_argument("--count", type=int, default=0, help="0 means run until interrupted.")
    args = parser.parse_args()
    run_sampler(
        provider=args.provider,
        command=args.command,
        output_path=args.output,
        interval_sec=args.interval_sec,
        count=args.count,
    )
    return 0


def _format_sample(sample: SystemMetricsSample) -> str:
    if not sample.available:
        return f"system_metrics provider={sample.provider} unavailable error={sample.error}"
    return (
        f"system_metrics provider={sample.provider} gpu={sample.gpu_active_percent}% "
        f"gpu_power={sample.gpu_total_power_w}W gpu_freq={sample.gpu_freq_mhz}MHz "
        f"thermal={sample.thermal_state}"
    )


def _get(data: dict[str, Any], *keys: str, default: object = None) -> object:
    for key in keys:
        if key in data:
            return data[key]
    return default


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _sum_optional(left: float | None, right: float | None) -> float | None:
    if left is None and right is None:
        return None
    return round((left or 0.0) + (right or 0.0), 6)


if __name__ == "__main__":
    raise SystemExit(main())
