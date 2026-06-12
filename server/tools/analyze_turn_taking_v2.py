from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any
from uuid import UUID

# Keywords that indicate user interruption or denial, potentially dangerous for early response.
DANGEROUS_KEYWORDS = ["待って", "ちょっと", "違う", "ちがう", "ダメ", "だめ", "ちがくて"]

# Helper regex to check if the tail consists only of auxiliary particles or punctuation.
SAFE_TAIL_REGEX = re.compile(r"^[、。？?ねよよねさかな JST\s]*$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze Turn-taking v2 shadow lane advisories vs main decisions.")
    parser.add_argument("--session-id", type=str, required=True, help="Conversation session ID to analyze.")
    parser.add_argument("--main", type=str, default="logs/turn-taking-main.jsonl", help="Path to main decision log.")
    parser.add_argument("--v2", type=str, default="logs/turn-taking-v2-shadow.jsonl", help="Path to v2 shadow advisory log.")
    parser.add_argument("--out", type=str, help="Path to save the output markdown report.")
    parser.add_argument("--html", action="store_true", help="HTMLタイムラインも同時に生成する")
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def classify_turn(main_rec: dict[str, Any], v2_recs: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Classifies the outcome of a single turn based on main and v2 records.
    """
    final_text = main_rec.get("text") or ""
    has_dangerous = any(kw in final_text for kw in DANGEROUS_KEYWORDS)

    # Find the first record where would_start_inference is True
    first_inf_rec = None
    for r in v2_recs:
        if r.get("would_start_inference") is True:
            first_inf_rec = r
            break

    outcome = "safe_wait"
    lead_time_ms = 0
    reason = "No early inference triggered."

    if first_inf_rec:
        stable_text = first_inf_rec.get("stable_text") or ""
        # Determine the suffix part
        if final_text.startswith(stable_text):
            tail = final_text[len(stable_text):]
        else:
            tail = final_text

        # Classify based on conditions
        if has_dangerous:
            outcome = "dangerous_speak"
            reason = f"v2 triggered early inference but final text contains dangerous keyword. stable: {stable_text!r}, final: {final_text!r}"
        elif SAFE_TAIL_REGEX.match(tail):
            # Safe tail (meaning matches)
            lead_time_ms = main_rec["ts_ms"] - first_inf_rec["ts_ms"]
            if lead_time_ms > 0:
                outcome = "good_early_prepare"
                reason = f"v2 successfully started inference early. Lead time: {lead_time_ms}ms"
            else:
                outcome = "safe_wait"
                reason = "v2 decided early but timestamp was not prior to main."
        else:
            # Meaning changed (not matched)
            outcome = "too_early_wrong"
            reason = f"v2 triggered early but final text changed semantic meaning. stable: {stable_text!r}, final: {final_text!r}"
    else:
        # No early inference triggered
        # Check if it was a missed opportunity
        # A missed opportunity is when final text is relatively long, but semantic saturation was high in one of the v2 states,
        # but would_start_inference was never True (e.g. VAD penalty was too high, etc.)
        was_saturating = any((r.get("semantic_saturation") or 0.0) >= 0.8 for r in v2_recs)
        if len(final_text) >= 7 and was_saturating:
            outcome = "missed_opportunity"
            reason = f"v2 remained silent but text was long and semantic saturation was high. final: {final_text!r}"
        else:
            outcome = "safe_wait"
            reason = "VAD wait was appropriate or response was too short for early prepare."

    # Phase TT-v2.10d: fusion（log-only 並走）の比較分析
    # fusion=True が main final よりどれだけ早いか / 早発火後にテキストが伸びたか（実機の言いさし率）
    first_fusion_rec = None
    for r in v2_recs:
        if r.get("would_start_inference_fusion") is True:
            first_fusion_rec = r
            break

    fusion_lead_time_ms = 0
    fusion_premature = False
    if first_fusion_rec:
        fusion_lead_time_ms = main_rec["ts_ms"] - first_fusion_rec["ts_ms"]
        fusion_stable = first_fusion_rec.get("stable_text") or ""
        # 発火時の stable_text より final が実質的に伸びていたら投機失敗（言いさし相当）
        if final_text.startswith(fusion_stable):
            fusion_tail = final_text[len(fusion_stable):]
        else:
            fusion_tail = final_text
        fusion_premature = not SAFE_TAIL_REGEX.match(fusion_tail)

    return {
        "outcome": outcome,
        "lead_time_ms": lead_time_ms,
        "reason": reason,
        "first_inf_rec": first_inf_rec,
        "fusion_fired": first_fusion_rec is not None,
        "fusion_lead_time_ms": fusion_lead_time_ms,
        "fusion_premature": fusion_premature,
    }


def generate_report(
    session_id: str,
    main_recs: list[dict[str, Any]],
    v2_recs: list[dict[str, Any]]
) -> str:
    # Filter by session_id
    main_filtered = [r for r in main_recs if r.get("conversation_session_id") == session_id]
    v2_filtered = [r for r in v2_recs if r.get("conversation_session_id") == session_id]

    # Group by turn_id
    turns: dict[str, dict[str, Any]] = {}
    for r in main_filtered:
        t_id = r.get("turn_id")
        if t_id:
            turns.setdefault(t_id, {"main": None, "v2": []})
            turns[t_id]["main"] = r

    for r in v2_filtered:
        t_id = r.get("turn_id")
        if t_id:
            turns.setdefault(t_id, {"main": None, "v2": []})
            turns[t_id]["v2"].append(r)

    # Filter out turns without main record
    valid_turns = {t_id: data for t_id, data in turns.items() if data["main"] is not None}

    # Statistics
    stats = {
        "good_early_prepare": 0,
        "too_early_wrong": 0,
        "missed_opportunity": 0,
        "safe_wait": 0,
        "dangerous_speak": 0,
    }
    lead_times = []
    fusion_fired_turns = 0
    fusion_premature_turns = 0
    fusion_lead_times: list[float] = []

    # Timeline entries
    timeline_lines = []
    # Find global start time to format timestamps relatively
    all_recs = main_filtered + v2_filtered
    if not all_recs:
        start_ts = 0
    else:
        start_ts = min(r["ts_ms"] for r in all_recs)

    # Process each turn
    for idx, (t_id, data) in enumerate(sorted(valid_turns.items(), key=lambda item: item[1]["main"]["ts_ms"]), 1):
        main_rec = data["main"]
        v2_list = sorted(data["v2"], key=lambda r: r["ts_ms"])

        analysis = classify_turn(main_rec, v2_list)
        outcome = analysis["outcome"]
        stats[outcome] += 1
        if outcome == "good_early_prepare":
            lead_times.append(analysis["lead_time_ms"])
        if analysis["fusion_fired"]:
            fusion_fired_turns += 1
            if analysis["fusion_premature"]:
                fusion_premature_turns += 1
            elif analysis["fusion_lead_time_ms"] > 0:
                fusion_lead_times.append(analysis["fusion_lead_time_ms"])

        timeline_lines.append(f"### Turn {idx}: {t_id}")
        timeline_lines.append(f"**Outcome**: `{outcome}` — {analysis['reason']}\n")
        timeline_lines.append("| Rel Time | Lane | Event / Info | Stable Text | Score |")
        timeline_lines.append("|---|---|---|---|---|")

        # Merge and sort all records of this turn
        turn_events = []
        for v2_r in v2_list:
            turn_events.append((v2_r["ts_ms"], "v2_shadow", v2_r))
        turn_events.append((main_rec["ts_ms"], "main", main_rec))
        turn_events.sort(key=lambda x: x[0])

        for ts, lane, rec in turn_events:
            rel_sec = (ts - start_ts) / 1000.0
            time_str = f"+{rel_sec:.2f}s"
            if lane == "v2_shadow":
                stable = rec.get("stable_text") or rec.get("text") or "-"
                score = rec.get("speech_decision_score")
                score_str = f"{score:.3f}" if score is not None else "-"
                proposal = rec.get("proposal") or "-"
                markers = ""
                if rec.get("would_start_inference") is True:
                    markers += " 🔵inf"
                if rec.get("would_start_inference_fusion") is True:
                    markers += " 🟠fusion"
                timeline_lines.append(
                    f"| {time_str} | `v2` | partial rev {rec.get('partial_revision', 0)} ({proposal}){markers} | {stable} | {score_str} |"
                )
            else:
                final = rec.get("text") or "-"
                timeline_lines.append(
                    f"| {time_str} | `main` | **final transcript ({rec.get('decision', '-')})** | **{final}** | - |"
                )
        timeline_lines.append("")

    total_proposals = len(v2_filtered)
    would_inf_count = sum(1 for r in v2_filtered if r.get("would_start_inference") is True)
    fusion_inf_count = sum(
        1 for r in v2_filtered if r.get("would_start_inference_fusion") is True
    )
    avg_lead = sum(lead_times) / len(lead_times) if lead_times else 0.0
    avg_fusion_lead = (
        sum(fusion_lead_times) / len(fusion_lead_times) if fusion_lead_times else 0.0
    )

    report_lines = [
        f"# Turn-taking v2 Analysis (Session: {session_id})",
        "",
        "## Summary",
        "",
        f"- **v2 proposals**: {total_proposals}",
        f"- **would_start_inference**: {would_inf_count}",
        f"- **valid early prepares (good_early_prepare)**: {stats['good_early_prepare']}",
        f"- **stale/discard needed (too_early_wrong)**: {stats['too_early_wrong']}",
        f"- **missed opportunities (missed_opportunity)**: {stats['missed_opportunity']}",
        f"- **safe waits (safe_wait)**: {stats['safe_wait']}",
        f"- **dangerous early proposals (dangerous_speak)**: {stats['dangerous_speak']}",
        f"- **average lead time**: {avg_lead:.1f}ms",
        "",
        "## Fusion (TT-v2.10 log-only) Comparison",
        "",
        f"- **would_start_inference_fusion records**: {fusion_inf_count}",
        f"- **turns where fusion fired**: {fusion_fired_turns}",
        f"- **fusion premature fires (final text grew after fire)**: {fusion_premature_turns}",
        f"- **average fusion lead time (clean fires)**: {avg_fusion_lead:.1f}ms",
        "",
        "## Timeline Details",
        "",
    ]
    report_lines.extend(timeline_lines)
    return "\n".join(report_lines)


def main() -> None:
    args = parse_args()
    main_path = Path(args.main)
    v2_path = Path(args.v2)

    main_recs = load_jsonl(main_path)
    v2_recs = load_jsonl(v2_path)

    report_md = generate_report(args.session_id, main_recs, v2_recs)

    if args.out:
        out_path = Path(args.out)
    else:
        out_path = Path("reports/turn-taking") / f"{args.session_id}.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report_md)

    print(f"Analysis report generated successfully at: {out_path}")

    if args.html:
        from server.tools.generate_timeline_html import generate_html_timeline
        html_path = out_path.with_suffix(".html")
        html_content = generate_html_timeline(args.session_id, main_recs, v2_recs)
        html_path.write_text(html_content, encoding="utf-8")
        print(f"HTML timeline generated at: {html_path}")


if __name__ == "__main__":
    main()
