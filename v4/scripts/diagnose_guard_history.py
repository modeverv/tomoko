"""MOCK: numeric recorded-callback counterfactual, not a live guard replay or adoption.

Compare current-voice latest, recent 250ms peak, and since-onset peak at the existing
sustained gate. Playing is a receiver-packet envelope proxy, not audible or engine
state. Runtime latest() is unknown; future callbacks/predictions are never selected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path

POLICIES = ("current", "recent_250ms", "since_onset")
ARMS = ("p3-10-v2", "p3-11-bc", "p3-12-ep", "p3-13-guard")


def _flag(value: object) -> bool:
    return value is True or str(value).lower() in {"true", "1", "yes", "on"}


def _offset(run: dict, trace: list[dict]) -> float | None:
    users = [e for e in run["events"] if e.get("type") == "text" and e.get("role") == "user"]
    stt = [e for e in trace if e["event"] == "stt_final"]
    if [e.get("text") for e in users] != [e.get("text") for e in stt]:
        raise ValueError("trace/run STT pairing mismatch")
    # Use only control events preceding the interjection, not later stop outcomes.
    offsets = [s["t"] - u["t"] for s, u in zip(stt, users, strict=True)
               if s["t"] < run["steps"][-1]["start"]]
    return statistics.median(offsets) if offsets else None


def _playing(run: dict, bc: set[int], receiver_t: float) -> tuple[bool, int | None]:
    placed, cursor, stopped = [], 0, -1
    for event in run["events"]:
        if event["t"] > receiver_t:
            break
        if event["type"] == "audio" and event.get("played"):
            generation = event["generation"]
            if generation <= stopped:
                continue
            start = max(round(event["t"] * 24000), cursor)
            cursor = start + event["n"]
            placed.append((start, cursor, generation))
        elif event["type"] == "stop":
            stopped = max(stopped, event["generation"])
            cut = round(event["t"] * 24000)
            placed = [(a, min(b, cut) if g <= stopped else b, g)
                      for a, b, g in placed if g > stopped or a < cut]
            cursor = max((b for _, b, _ in placed), default=0)
    position = round(receiver_t * 24000)
    generation = next((g for a, b, g in placed if a <= position < b and g not in bc), None)
    return generation is not None, generation


def _decision(frame: dict | None, gate: float, threshold: float, reason: str = "") -> dict:
    value = None if frame is None else frame["p_bc_det_user"]
    return {
        "decision": "stop" if frame is None or reason or value < threshold else "hold",
        "reason": reason or ("no_eligible_detector_frame" if frame is None
                             else "threshold_met" if value >= threshold else "below_threshold"),
        "value": value, "source_at_s": None if frame is None else frame["at_s"],
        "age_s": None if frame is None else gate - frame["at_s"],
    }


def _peak(frames: list[dict]) -> dict | None:
    return max(frames, key=lambda item: item["p_bc_det_user"], default=None)


def analyze_case(run: dict, trace: list[dict]) -> dict:
    """No models/audio; preserve no transcript fields in the returned evidence."""
    if run.get("private") is not False:
        raise ValueError("run private must be explicitly false")
    config = next(e for e in trace if e["event"] == "config")
    threshold, fresh = float(config["guard_bc_det"]), float(config["maai_stale_s"])
    offset = _offset(run, trace)
    bc = {e["generation"] for e in trace if e["event"] == "backchannel"}
    target = run["steps"][-1]
    frames, cpc, gates = [], [], []
    voice = None
    for index, event in enumerate(trace):
        kind = event["event"]
        if kind == "maai":
            if "p_bc_det_user" in event:
                frames.append({k: event[k] for k in ("t", "audio_s", "at_s", "p_bc_det_user")})
            if "p_now" in event:
                cpc.append(event["at_s"])
        elif kind == "vad_start":
            voice = event
        elif kind in {"vad_end", "vad_drop"}:
            voice = None
        elif (kind == "vad_sustained" and voice is not None
              and target["start"] - .1 <= voice["at_s"] <= target["end"]
              and event["at_s"] >= target["start"]):
            at = event["at_s"]
            eligible = [f for f in frames if voice["at_s"] <= f["at_s"] <= at
                        and 0 <= f["audio_s"] - f["at_s"] <= fresh + 1e-9]
            latest = eligible[-1] if eligible else None
            recent = [f for f in eligible if at - f["at_s"] <= .25 + 1e-9]
            composite_at = None if latest is None else latest["at_s"]
            past_cpc = [t for t in cpc if t <= at]
            if composite_at is not None and _flag(config.get("maai_vap_cpc")) and past_cpc:
                composite_at = min(composite_at, past_cpc[-1])
            stale = composite_at is not None and at - composite_at > fresh + 1e-9
            decisions = {
                "current": _decision(latest, at, threshold,
                                     "stale_composite_frame" if stale else ""),
                "recent_250ms": _decision(_peak(recent), at, threshold),
                "since_onset": _decision(_peak(eligible), at, threshold),
            }
            playing_start, gen_start = ((None, None) if offset is None else
                                       _playing(run, bc, voice["t"] - offset))
            playing_gate, gen_gate = ((None, None) if offset is None else
                                     _playing(run, bc, event["t"] - offset))
            reason = ("barge_in_disabled" if not _flag(config.get("barge_in")) else
                      "guard_disabled" if not _flag(config.get("barge_guard")) else
                      "playing_unknown" if playing_start is None or playing_gate is None else
                      "no_main_playback_at_start" if not playing_start else
                      "no_main_playback_at_gate" if not playing_gate else
                      "main_generation_changed" if gen_start != gen_gate else "")
            if reason:
                for decision in decisions.values():
                    decision.update(decision="unknown" if reason in {
                        "playing_unknown", "main_generation_changed"} else "not_eligible", reason=reason)
            gates.append({
                "voice_start_s": voice["at_s"], "gate_at_s": at, "gate_server_t": event["t"],
                "playing_at_start": playing_start, "playing_at_gate": playing_gate,
                "generation_at_start": gen_start, "generation_at_gate": gen_gate,
                "playing_evidence": "receiver_envelope_proxy", "runtime_latest_known": False,
                "threshold": threshold, "legacy_composite_at_s": composite_at,
                "decisions": decisions, "eligible_detector_frames": eligible,
                # Outcome is separate from selection: never used to choose a detector value.
                "observed_stop_within_50ms": any(e["event"] == "stop"
                    and 0 <= e["t"] - event["t"] <= .05 for e in trace[index + 1:]),
            })
    counts = Counter(e["event"] for e in trace if e.get("audio_s", -1) >= target["start"])
    return {
        "id": run["id"], "axis": run["axis"], "private": False,
        "receiver_to_trace_offset_s": offset, "gates": gates,
        "no_gate_reason": None if gates else "no_sustained_gate",
        "interjection_events": {k: counts[k] for k in (
            "vad_drop", "ignored_short_voice", "barge_guard_hold", "barge_guard_release")},
    }


def summarize(cases: list[dict]) -> dict:
    decisions = {p: Counter() for p in POLICIES}
    reasons = {p: Counter() for p in POLICIES}
    harm = Counter()
    for case in cases:
        for row in case["gates"]:
            for policy in POLICIES:
                choice = row["decisions"][policy]
                decisions[policy][choice["decision"]] += 1
                reasons[policy][choice["reason"]] += 1
                if (case["axis"] == "T4" and row["decisions"]["current"]["decision"] == "stop"
                        and choice["decision"] == "hold"):
                    harm[policy] += 1
    return {
        "cases": len(cases), "gate_count": sum(len(c["gates"]) for c in cases),
        "cases_without_gate": sum(not c["gates"] for c in cases),
        "decisions": {p: dict(v) for p, v in decisions.items()},
        "nonfiring_reasons": {p: dict(v) for p, v in reasons.items()},
        "t4_additional_hold_candidates": {p: harm[p] for p in POLICIES},
        "measured_policy_improvement": False,
    }


def collect(data_root: Path) -> list[dict]:
    scenarios = Path(__file__).resolve().parents[1] / "scenarios/small-v2"
    ids = sorted(r["id"] for p in scenarios.glob("*.json") for r in json.loads(p.read_text()))
    if len(ids) != 55 or len(set(ids)) != 55:
        raise ValueError("expected the 55 public synthetic scenario IDs")
    selected = [sid for sid in ids if sid.startswith(("t3-", "t4-"))]
    cases = []
    for arm in ARMS:
        paths = sorted((data_root / "traces" / arm).glob("*.jsonl"))
        if len(paths) < 165 or [int(p.stem.rsplit("-", 1)[1]) for p in paths[:165]] != list(range(165)):
            raise ValueError("expected three contiguous sequential trace repetitions")
        for repetition in range(1, 4):
            for sid in selected:
                run_path = data_root / "runs" / f"{arm}-{repetition}" / sid / "run.json"
                run_bytes = run_path.read_bytes()
                run = json.loads(run_bytes)
                if run.get("private") is not False or run["id"] != sid:
                    raise ValueError("private or incorrectly identified synthetic run")
                trace_path = paths[(repetition - 1) * 55 + ids.index(sid)]
                trace_bytes = trace_path.read_bytes()
                trace = [json.loads(line) for line in trace_bytes.splitlines()]
                row = analyze_case(run, trace)
                row.update(run=f"{arm}-{repetition}", trace_file=trace_path.name,
                           run_sha256=hashlib.sha256(run_bytes).hexdigest(),
                           trace_sha256=hashlib.sha256(trace_bytes).hexdigest())
                cases.append(row)
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="numeric artifact directory")
    args = parser.parse_args()
    cases = collect(args.data_root)
    report = {
        "schema_version": 1, "kind": "MOCK_counterfactual_callback_replay",
        "limits": ["No live policy adoption or closed-loop performance measurement",
                   "Playing uses full receiver clip envelopes, not audible intervals or engine state",
                   "Runtime worker latest may precede its logged callback and is unknown",
                   "Current selector is scoped to the current voice; pre-onset/future values excluded"],
        "summary": summarize(cases),
        "by_run": {name: summarize([c for c in cases if c["run"] == name])
                   for name in sorted({c["run"] for c in cases})},
    }
    args.out.mkdir(parents=True, exist_ok=True)
    for name, value in [("gates.json", cases), ("summary.json", report)]:
        (args.out / name).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report["summary"], allow_nan=False))


if __name__ == "__main__":
    main()
