"""Numeric-only adapter for the two explicitly authorized synthetic c2-08 rechecks."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


def collect(runtime_path: Path, arm: str, helper_path: Path) -> dict:
    spec = importlib.util.spec_from_file_location("maai_observation_helper", helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    raw_runtime = runtime_path.read_bytes()
    runtime = json.loads(raw_runtime)
    index = {"repeat1": 1, "repeat2": 2}[arm]
    meta = runtime[arm]
    allowed = Path("/Users/seijiro/Sync/sync_work/by-llms/tomoko/v4/work/runs")
    root = Path(meta["run_dir"])
    if root.resolve() != (allowed / f"p3-20-c2-08-recheck-{index}").resolve():
        raise ValueError("recheck run outside explicit synthetic scope")
    run_paths = sorted(root.glob("*/run.json"))
    if [p.parent.name for p in run_paths] != ["c2-08"]:
        raise ValueError("expected exactly c2-08")
    raw_run = run_paths[0].read_bytes()
    run = json.loads(raw_run)
    if run.get("private") is not False or run["id"] != "c2-08":
        raise ValueError("synthetic privacy/id validation failed before trace reads")
    paths = meta["trace_files"]
    if len(paths) != 1 or meta["source_sha256"] != runtime["full"]["source_sha256"]:
        raise ValueError("expected one pinned trace and identical frozen source")
    trace_path = Path(paths[0])
    allowed_trace = allowed.parent / "traces/p3-19-signal-freshness-final"
    if trace_path.parent.resolve() != allowed_trace.resolve():
        raise ValueError("trace outside explicit synthetic scope")
    trace, trace_sha, complete = helper.read_trace(trace_path)
    if not complete:
        raise ValueError("incomplete trace")
    row = helper.analyze_pair(run, trace)
    row.update(source_run_sha256=helper.sha(raw_run), source_trace_sha256=trace_sha,
               trace_name=trace_path.name, trace_complete_lines=True)
    config = helper.normalized_config(next(e for e in trace if e["event"] == "config"))
    safe = {k: v for k, v in config.items() if isinstance(v, (float, int, bool)) or v is None
            or k in {"llm_model", "stt", "stt_model", "tts", "tts_voice", "maai_modes", "ep_signal"}}
    return {"schema_version": 1, "arm": arm, "partial_snapshot": False,
        "source_verification": helper.source_check(runtime, meta), "cases": [row],
        "summary": {"matched_cases": 1, "trace_files_seen": 1, "unpaired_traces": 0,
            "counts": row["counts"], "workers": row["workers"], "signal_reads": row["signal_reads"]},
        "configuration": {"identical_across_cases": True,
            "sha256": helper.sha(json.dumps(config, sort_keys=True).encode()), "settings": safe},
        "runtime_sha256": helper.sha(raw_runtime),
        "analysis_script_sha256": helper.sha(helper_path.read_bytes()),
        "analysis_adapter_sha256": helper.sha(Path(__file__).read_bytes()),
        "limits": ["Explicitly authorized one-case recheck; shared helper analysis functions unchanged",
            "Perf durations subtract perf timestamps only; signal ages use input timestamps only",
            "Reads/process calls are correlated; these repeats do not establish an improvement rate",
            "Legacy comparison is conditional on candidate snapshots, not old closed-loop replay"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--arm", choices=("repeat1", "repeat2"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = collect(args.runtime, args.arm,
                     Path(__file__).with_name("aggregate_maai_observations.py"))
    args.out.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"arm": args.arm, "source_matches": report["source_verification"]["matches"],
                      "counts": report["summary"]["counts"]}))


if __name__ == "__main__":
    main()
