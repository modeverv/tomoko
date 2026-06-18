# ruff: noqa: E501
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MOTIVES = ("curiosity", "teasing", "attachment", "unspoken")
DEFAULT_PARAMS: dict[str, float] = {
    "curiosity_gain": 0.35,
    "teasing_gain": 0.28,
    "attachment_gain": 0.22,
    "unspoken_gain": 0.30,
    "floor_weight": 0.16,
    "freshness_weight": 0.18,
    "intrusion_weight": 0.32,
    "user_speaking_penalty": 0.85,
    "tomoko_speaking_penalty": 0.75,
    "rejection_penalty": 0.45,
    "silence_attachment_gain": 0.018,
    "decay_sec": 300.0,
    "threshold": 0.65,
}
DEFAULT_THRESHOLDS = (0.55, 0.65, 0.75)
STOP_KEYWORDS = ("止め", "ストップ", "黙", "うるさい", "静かに", "待って")


@dataclass(frozen=True)
class CandidateView:
    id: str
    source: str
    seed: str
    generated_text: str | None
    priority: float
    urgent: bool
    maturity: int
    created_at_ms: int
    expires_at_ms: int
    spoken_at_ms: int | None
    dismissed_at_ms: int | None
    context_tags: tuple[str, ...]
    metadata_json: dict[str, Any]
    lifecycle: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> CandidateView:
        return cls(
            id=str(payload["id"]),
            source=str(payload.get("source") or "unknown"),
            seed=str(payload.get("seed") or ""),
            generated_text=optional_str(payload.get("generated_text")),
            priority=float(payload.get("priority") or 0.0),
            urgent=bool(payload.get("urgent")),
            maturity=int(payload.get("maturity") or 0),
            created_at_ms=parse_time_ms(payload.get("created_at")),
            expires_at_ms=parse_time_ms(payload.get("expires_at")),
            spoken_at_ms=parse_optional_time_ms(payload.get("spoken_at")),
            dismissed_at_ms=parse_optional_time_ms(payload.get("dismissed_at")),
            context_tags=tuple(str(tag) for tag in payload.get("context_tags") or ()),
            metadata_json=dict(payload.get("metadata_json") or {}),
            lifecycle=str(payload.get("lifecycle") or "unknown"),
        )

    def is_available_at(self, ts_ms: int) -> bool:
        return (
            self.created_at_ms <= ts_ms
            and self.expires_at_ms > ts_ms
            and (self.spoken_at_ms is None or self.spoken_at_ms > ts_ms)
            and (self.dismissed_at_ms is None or self.dismissed_at_ms > ts_ms)
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "seed": self.seed,
            "generated_text": self.generated_text,
            "priority": self.priority,
            "urgent": self.urgent,
            "maturity": self.maturity,
            "created_at_ms": self.created_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "spoken_at_ms": self.spoken_at_ms,
            "dismissed_at_ms": self.dismissed_at_ms,
            "context_tags": list(self.context_tags),
            "metadata_json": self.metadata_json,
            "lifecycle": self.lifecycle,
        }


@dataclass(frozen=True)
class TimelineEvent:
    ts_ms: int
    lane: str
    event: str
    text: str
    payload: dict[str, Any]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def candidate_lifecycle(payload: dict[str, Any], now: datetime) -> str:
    if payload.get("spoken_at") is not None:
        return "spoken"
    if payload.get("dismissed_at") is not None:
        return "dismissed"
    expires_at = parse_datetime(payload.get("expires_at"))
    if expires_at <= now:
        return "expired"
    return "active"


def arrival_lifecycle(payload: dict[str, Any], now: datetime) -> str:
    if payload.get("used_at") is not None:
        return "used"
    valid_until = parse_datetime(payload.get("valid_until"))
    if valid_until <= now:
        return "expired"
    return "fresh"


def load_candidate_export(path: Path | None) -> list[CandidateView]:
    if path is None or not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        CandidateView.from_payload(item)
        for item in payload.get("utterance_candidates", [])
    ]


def records_to_events(records: list[dict[str, Any]]) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for record in records:
        ts_ms = int(record.get("ts_ms") or 0)
        if ts_ms <= 0:
            continue
        events.append(
            TimelineEvent(
                ts_ms=ts_ms,
                lane=str(record.get("lane") or "unknown"),
                event=str(record.get("event") or "event"),
                text=str(record.get("text") or record.get("stable_text") or ""),
                payload=record,
            )
        )
    return sorted(events, key=lambda event: event.ts_ms)


def simulate_from_logs(
    *,
    main_records: list[dict[str, Any]],
    v2_records: list[dict[str, Any]],
    candidates: list[CandidateView],
    params: dict[str, float] | None = None,
    step_sec: float = 1.0,
    thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS,
) -> dict[str, Any]:
    events = records_to_events(main_records + v2_records)
    if not events:
        start_ms = now_ms()
        end_ms = start_ms + 60_000
    else:
        start_ms = min(event.ts_ms for event in events)
        end_ms = max(event.ts_ms for event in events) + 10_000
    return simulate_range(
        start_ms=start_ms,
        end_ms=end_ms,
        events=events,
        candidates=candidates,
        params=params,
        step_sec=step_sec,
        thresholds=thresholds,
    )


def simulate_recent_sessions_from_logs(
    *,
    main_records: list[dict[str, Any]],
    v2_records: list[dict[str, Any]],
    candidates: list[CandidateView],
    limit: int = 100,
    params: dict[str, float] | None = None,
    step_sec: float = 1.0,
    thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS,
) -> dict[str, Any]:
    groups = recent_session_groups(main_records, v2_records, limit=limit)
    sessions: list[dict[str, Any]] = []
    for index, group in enumerate(groups, start=1):
        simulation = simulate_from_logs(
            main_records=group["main_records"],
            v2_records=group["v2_records"],
            candidates=candidates,
            params=params,
            step_sec=step_sec,
            thresholds=thresholds,
        )
        prefix_fire_marker_ids(simulation, prefix=f"s{index}")
        sessions.append(
            {
                "session_id": group["session_id"],
                "label": group["label"],
                "start_ts_ms": group["start_ts_ms"],
                "end_ts_ms": group["end_ts_ms"],
                "event_count": group["event_count"],
                "simulation": simulation,
            }
        )
    if not sessions:
        fallback = simulate_from_logs(
            main_records=main_records,
            v2_records=v2_records,
            candidates=candidates,
            params=params,
            step_sec=step_sec,
            thresholds=thresholds,
        )
        return fallback | {"mode": "single_session_fallback"}
    return {
        "schema_version": 1,
        "mode": "multi_session",
        "generated_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "session_count": len(sessions),
        "sessions": sessions,
        "summary": {
            "session_count": len(sessions),
            "total_snapshot_count": sum(
                item["simulation"]["summary"]["snapshot_count"] for item in sessions
            ),
            "total_fire_marker_count": sum(
                item["simulation"]["summary"]["fire_marker_count"] for item in sessions
            ),
        },
    }


def recent_session_groups(
    main_records: list[dict[str, Any]],
    v2_records: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for record in main_records:
        ts_ms = int(record.get("ts_ms") or 0)
        session_id = record.get("conversation_session_id")
        if ts_ms <= 0 or session_id is None:
            continue
        key = str(session_id)
        group = grouped.setdefault(
            key,
            {
                "session_id": key,
                "main_records": [],
                "v2_records": [],
                "start_ts_ms": ts_ms,
                "end_ts_ms": ts_ms,
                "event_count": 0,
                "turn_count": 0,
            },
        )
        group["main_records"].append(record)
        group["start_ts_ms"] = min(group["start_ts_ms"], ts_ms)
        group["end_ts_ms"] = max(group["end_ts_ms"], ts_ms)
        group["event_count"] += 1
        group["turn_count"] += 1

    if not grouped:
        for record in v2_records:
            ts_ms = int(record.get("ts_ms") or 0)
            session_id = record.get("conversation_session_id")
            if ts_ms <= 0 or session_id is None:
                continue
            key = str(session_id)
            group = grouped.setdefault(
                key,
                {
                    "session_id": key,
                    "main_records": [],
                    "v2_records": [],
                    "start_ts_ms": ts_ms,
                    "end_ts_ms": ts_ms,
                    "event_count": 0,
                    "turn_count": 0,
                },
            )
            group["v2_records"].append(record)
            group["start_ts_ms"] = min(group["start_ts_ms"], ts_ms)
            group["end_ts_ms"] = max(group["end_ts_ms"], ts_ms)
            group["event_count"] += 1

    groups = sorted(grouped.values(), key=lambda item: item["end_ts_ms"], reverse=True)
    selected_session_ids = {group["session_id"] for group in groups[:limit]}
    for record in v2_records:
        ts_ms = int(record.get("ts_ms") or 0)
        session_id = record.get("conversation_session_id")
        if ts_ms <= 0 or session_id is None:
            continue
        key = str(session_id)
        if key not in selected_session_ids or not grouped[key]["main_records"]:
            continue
        group = grouped[key]
        group["v2_records"].append(record)
        group["start_ts_ms"] = min(group["start_ts_ms"], ts_ms)
        group["end_ts_ms"] = max(group["end_ts_ms"], ts_ms)
        group["event_count"] += 1

    groups = sorted(grouped.values(), key=lambda item: item["end_ts_ms"], reverse=True)[
        :limit
    ]
    for index, group in enumerate(groups, start=1):
        started = datetime.fromtimestamp(group["start_ts_ms"] / 1000, UTC).astimezone()
        group["label"] = (
            f"{index}. {started.strftime('%Y-%m-%d %H:%M:%S')} "
            f"{group['session_id'][:8]} turns={group['turn_count']} "
            f"events={group['event_count']}"
        )
    return groups


def prefix_fire_marker_ids(simulation: dict[str, Any], *, prefix: str) -> None:
    for marker in simulation.get("fire_markers", []):
        marker["id"] = f"{prefix}-{marker['id']}"


def simulate_silence(
    *,
    candidates: list[CandidateView],
    start_ms: int | None = None,
    duration_sec: int = 300,
    params: dict[str, float] | None = None,
    step_sec: float = 1.0,
    thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS,
) -> dict[str, Any]:
    start = start_ms or now_ms()
    return simulate_range(
        start_ms=start,
        end_ms=start + duration_sec * 1000,
        events=[],
        candidates=candidates,
        params=params,
        step_sec=step_sec,
        thresholds=thresholds,
    )


def simulate_range(
    *,
    start_ms: int,
    end_ms: int,
    events: list[TimelineEvent],
    candidates: list[CandidateView],
    params: dict[str, float] | None,
    step_sec: float,
    thresholds: tuple[float, ...],
) -> dict[str, Any]:
    model_params = {**DEFAULT_PARAMS, **(params or {})}
    pressures = {motive: 0.0 for motive in MOTIVES}
    snapshots: list[dict[str, Any]] = []
    fire_markers: list[dict[str, Any]] = []
    event_index = 0
    recent_events: list[TimelineEvent] = []
    recent_rejection_until = 0
    step_ms = max(1, int(step_sec * 1000))
    previous_ms = start_ms
    marker_seq = 0

    for ts_ms in range(start_ms, end_ms + 1, step_ms):
        elapsed_sec = max(0.0, (ts_ms - previous_ms) / 1000.0)
        previous_ms = ts_ms
        while event_index < len(events) and events[event_index].ts_ms <= ts_ms:
            event = events[event_index]
            recent_events.append(event)
            if event.lane == "main" and any(word in event.text for word in STOP_KEYWORDS):
                recent_rejection_until = max(recent_rejection_until, event.ts_ms + 60_000)
            event_index += 1

        recent_events = [event for event in recent_events if ts_ms - event.ts_ms <= 120_000]
        user_speaking = any(
            event.lane == "main"
            and event.event == "final_transcript_received"
            and 0 <= ts_ms - event.ts_ms <= 1800
            for event in recent_events
        )
        tomoko_speaking = any(
            event.lane == "main"
            and event.payload.get("playback_state") in {"speaking", "client_playing"}
            and 0 <= ts_ms - event.ts_ms <= 3000
            for event in recent_events
        )
        floor_available = not user_speaking and not tomoko_speaking
        available_candidates = [c for c in candidates if c.is_available_at(ts_ms)]
        top_candidate = select_top_candidate(available_candidates)
        decay_pressures(pressures, elapsed_sec, model_params["decay_sec"])
        apply_candidate_pressure(pressures, top_candidate, model_params)
        if floor_available:
            pressures["attachment"] = clamp01(
                pressures["attachment"]
                + model_params["silence_attachment_gain"] * elapsed_sec
            )
        score = compute_score(
            pressures=pressures,
            candidate=top_candidate,
            floor_available=floor_available,
            user_speaking=user_speaking,
            tomoko_speaking=tomoko_speaking,
            rejection_active=ts_ms < recent_rejection_until,
            params=model_params,
        )
        dominant_motive = max(pressures, key=lambda key: pressures[key])
        would_fire = {f"{threshold:.2f}": score >= threshold for threshold in thresholds}
        snapshot = {
            "ts_ms": ts_ms,
            "relative_sec": round((ts_ms - start_ms) / 1000.0, 3),
            "user_speaking": user_speaking,
            "tomoko_speaking": tomoko_speaking,
            "floor_available": floor_available,
            "candidate_count": len(available_candidates),
            "top_candidate_id": top_candidate.id if top_candidate else None,
            "top_candidate_source": top_candidate.source if top_candidate else None,
            "top_candidate_text": candidate_text(top_candidate),
            "curiosity_pressure": round(pressures["curiosity"], 4),
            "teasing_pressure": round(pressures["teasing"], 4),
            "attachment_pressure": round(pressures["attachment"], 4),
            "unspoken_pressure": round(pressures["unspoken"], 4),
            "speak_score": round(score, 4),
            "dominant_motive": dominant_motive,
            "would_fire": would_fire,
            "recent_text": recent_conversation_text(recent_events),
        }
        snapshot["prompt_preview"] = build_prompt_preview(snapshot, top_candidate)
        snapshots.append(snapshot)
        if score >= model_params["threshold"] and floor_available:
            marker_seq += 1
            fire_markers.append(
                {
                    "id": f"marker-{marker_seq}",
                    "ts_ms": ts_ms,
                    "relative_sec": snapshot["relative_sec"],
                    "score": snapshot["speak_score"],
                    "threshold": model_params["threshold"],
                    "dominant_motive": dominant_motive,
                    "top_candidate_id": snapshot["top_candidate_id"],
                    "prompt_preview": snapshot["prompt_preview"],
                }
            )

    return {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "start_ts_ms": start_ms,
        "end_ts_ms": end_ms,
        "params": model_params,
        "thresholds": list(thresholds),
        "events": [event_to_json(event, start_ms) for event in events],
        "candidates": [candidate.to_json() for candidate in candidates],
        "snapshots": snapshots,
        "fire_markers": fire_markers,
        "summary": summarize_snapshots(snapshots, fire_markers, thresholds),
    }


def select_top_candidate(candidates: list[CandidateView]) -> CandidateView | None:
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda c: (
            c.maturity >= 1 and c.generated_text is not None,
            c.urgent,
            c.priority,
            -c.created_at_ms,
        ),
    )


def apply_candidate_pressure(
    pressures: dict[str, float],
    candidate: CandidateView | None,
    params: dict[str, float],
) -> None:
    if candidate is None:
        return
    motive = candidate_motive(candidate)
    strength = candidate_motive_strength(candidate)
    gain = params.get(f"{motive}_gain", 0.2)
    pressures[motive] = clamp01(pressures[motive] + strength * gain * 0.05)
    if candidate.lifecycle in {"dismissed", "expired", "spoken"}:
        pressures["unspoken"] = clamp01(
            pressures["unspoken"] + params.get("unspoken_gain", 0.2) * 0.01
        )


def compute_score(
    *,
    pressures: dict[str, float],
    candidate: CandidateView | None,
    floor_available: bool,
    user_speaking: bool,
    tomoko_speaking: bool,
    rejection_active: bool,
    params: dict[str, float],
) -> float:
    candidate_priority = candidate.priority if candidate is not None else 0.0
    intrusion = candidate_intrusion_risk(candidate)
    score = (
        pressures["curiosity"]
        + pressures["teasing"]
        + pressures["attachment"]
        + pressures["unspoken"]
        + (params["floor_weight"] if floor_available else 0.0)
        + params["freshness_weight"] * candidate_priority
        - params["intrusion_weight"] * intrusion
        - (params["user_speaking_penalty"] if user_speaking else 0.0)
        - (params["tomoko_speaking_penalty"] if tomoko_speaking else 0.0)
        - (params["rejection_penalty"] if rejection_active else 0.0)
    )
    return clamp01(score)


def build_prompt_preview(
    snapshot: dict[str, Any],
    candidate: CandidateView | None,
) -> str:
    motive = str(snapshot["dominant_motive"])
    candidate_line = candidate_text(candidate) or "候補なし"
    recent = snapshot.get("recent_text") or "近くの会話ログなし"
    directive = motive_directive(motive)
    return "\n".join(
        [
            "## INITIATIVE MOTIVATION SNAPSHOT",
            f"dominant_motive: {motive}",
            f"speak_score: {snapshot['speak_score']}",
            f"floor_available: {snapshot['floor_available']}",
            "",
            "## INTEREST / CANDIDATE",
            candidate_line,
            "",
            "## NEARBY CONVERSATION",
            recent,
            "",
            "## MOTIVE DIRECTIVE",
            directive,
            "",
            "## OUTPUT CONTRACT",
            "横槍または自発発話として、一言だけ。長く説明せず、相手が返しやすい形にする。",
        ]
    )


def motive_directive(motive: str) -> str:
    if motive == "teasing":
        return (
            "Tomoko は今、少しだけ「ちょっとちょっかいをかけたい」気持ちがある。\n"
            "ただし相手の作業を邪魔しすぎないように、短く軽い一言にする。"
        )
    if motive == "curiosity":
        return (
            "Tomoko は今、画面や会話で起きていることが気になっている。\n"
            "詰問ではなく、ふと気になった感じで短く聞く。"
        )
    if motive == "attachment":
        return (
            "Tomoko は少し構ってほしい気持ちがある。\n"
            "重くならず、相手の反応を待てる短い声かけにする。"
        )
    return (
        "Tomoko は言いそびれたことが少し残っている。\n"
        "会話の流れを壊さないように、短く補足する。"
    )


def render_html(simulation: dict[str, Any]) -> str:
    data_json = json.dumps(simulation, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Initiative Motivation Sandbox</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 0; background: #f7f7f4; color: #222; }}
main {{ display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 16px; padding: 16px; }}
section {{ background: white; border: 1px solid #ddd; border-radius: 8px; padding: 12px; margin-bottom: 12px; }}
h1 {{ font-size: 20px; margin: 0 0 12px; }}
h2 {{ font-size: 15px; margin: 0 0 8px; }}
label {{ display: grid; grid-template-columns: 1fr 72px; gap: 8px; align-items: center; font-size: 13px; margin: 8px 0; }}
input[type=range] {{ width: 100%; }}
canvas {{ width: 100%; height: 360px; border: 1px solid #ddd; background: #fff; }}
.timeline {{ max-height: 220px; overflow: auto; font-size: 12px; line-height: 1.5; }}
.event {{ border-left: 3px solid #999; padding-left: 8px; margin: 6px 0; }}
.fire {{ border-left-color: #d04a02; background: #fff4ec; cursor: pointer; }}
.control-group {{ border: 1px solid #ddd; border-radius: 8px; padding: 10px; margin: 10px 0; }}
.control-group h3 {{ font-size: 13px; margin: 0 0 8px; }}
.control-group p {{ font-size: 11px; line-height: 1.35; margin: -2px 0 8px; color: #555; }}
.control-group.persona {{ background: #f4f0ff; border-color: #d7c9ff; }}
.control-group.timing {{ background: #eef7f2; border-color: #bfe0cb; }}
pre, textarea {{ width: 100%; box-sizing: border-box; white-space: pre-wrap; font-size: 12px; }}
textarea {{ min-height: 180px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
.muted {{ color: #666; }}
</style>
</head>
<body>
<main>
<div>
<h1>Initiative Motivation Sandbox</h1>
<section>
<h2>Chart</h2>
<canvas id="chart" width="1200" height="360"></canvas>
<p class="muted">Lines: curiosity / teasing / attachment / unspoken / speak score. Orange ticks are would-fire markers.</p>
</section>
<section>
<h2>Timeline</h2>
<div id="timeline" class="timeline"></div>
</section>
<section>
<h2>Prompt Preview</h2>
<pre id="preview">Select a fire marker.</pre>
</section>
</div>
<aside>
<section id="sessionSection">
<h2>Session</h2>
<select id="sessionSelect" style="width:100%"></select>
</section>
<section>
<h2>Parameters</h2>
<div id="controls"></div>
</section>
<section>
<h2>Candidate JSON</h2>
<textarea id="candidateJson"></textarea>
</section>
<section>
<h2>Summary</h2>
<pre id="summary"></pre>
</section>
</aside>
</main>
<script>
const original = {data_json};
const controlKeys = [
  "curiosity_gain",
  "teasing_gain",
  "attachment_gain",
  "unspoken_gain",
  "silence_attachment_gain",
  "floor_weight",
  "freshness_weight",
  "intrusion_weight",
  "user_speaking_penalty",
  "tomoko_speaking_penalty",
  "rejection_penalty",
  "threshold",
  "decay_sec"
];
const controlGroups = [
  {{
    id: "persona",
    title: "Persona / Motive",
    note: "Tomoko がどの理由で話したくなるかを決める係数。",
    keys: ["curiosity_gain", "teasing_gain", "attachment_gain", "unspoken_gain", "silence_attachment_gain"]
  }},
  {{
    id: "timing",
    title: "Timing / Gate",
    note: "今この瞬間に割り込んでよいか、発火を通すかを決める係数。",
    keys: ["floor_weight", "freshness_weight", "intrusion_weight", "user_speaking_penalty", "tomoko_speaking_penalty", "rejection_penalty", "threshold", "decay_sec"]
  }}
];
const motiveKeys = ["curiosity", "teasing", "attachment", "unspoken"];
const stopKeywords = ["止め", "ストップ", "黙", "うるさい", "静かに", "待って"];
const sessionSelect = document.getElementById("sessionSelect");
const sessionSection = document.getElementById("sessionSection");
if (original.sessions) {{
  original.sessions.forEach((item, index) => {{
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = item.label;
    sessionSelect.append(option);
  }});
  sessionSelect.onchange = () => {{
    current = getCurrentSimulation();
    Object.assign(params, current.params);
    syncControls();
    setCandidateJson();
    redraw();
  }};
}} else {{
  sessionSection.style.display = "none";
}}
let current = getCurrentSimulation();
const params = Object.assign({{}}, current.params);
const controls = document.getElementById("controls");
for (const group of controlGroups) {{
  const groupRoot = document.createElement("div");
  groupRoot.className = `control-group ${{group.id}}`;
  const heading = document.createElement("h3");
  heading.textContent = group.title;
  const note = document.createElement("p");
  note.textContent = group.note;
  groupRoot.append(heading, note);
  for (const key of group.keys) {{
    groupRoot.append(createControl(key, group.id));
  }}
  controls.append(groupRoot);
}}
function createControl(key, groupId) {{
  const label = document.createElement("label");
  label.dataset.controlKind = groupId;
  const span = document.createElement("span");
  span.textContent = key;
  const input = document.createElement("input");
  input.type = "range";
  input.min = key === "decay_sec" ? "30" : "0";
  input.max = key === "decay_sec" ? "1800" : (key === "silence_attachment_gain" ? "0.1" : "1");
  input.step = key === "decay_sec" ? "10" : (key === "silence_attachment_gain" ? "0.001" : "0.01");
  input.value = params[key];
  const value = document.createElement("output");
  value.textContent = input.value;
  input.oninput = () => {{ params[key] = Number(input.value); value.textContent = input.value; redraw(); }};
  input.dataset.paramKey = key;
  value.dataset.paramValueFor = key;
  label.append(span, input, value);
  return label;
}}
function getCurrentSimulation() {{
  if (!original.sessions) return original;
  const index = Number(sessionSelect ? sessionSelect.value || "0" : "0");
  return original.sessions[index].simulation;
}}
function syncControls() {{
  document.querySelectorAll("input[data-param-key]").forEach(input => {{
    input.value = params[input.dataset.paramKey];
  }});
  document.querySelectorAll("output[data-param-value-for]").forEach(output => {{
    output.textContent = params[output.dataset.paramValueFor];
  }});
}}
function setCandidateJson() {{
  document.getElementById("candidateJson").value = JSON.stringify(current.candidates || [], null, 2);
}}
document.getElementById("candidateJson").addEventListener("input", redraw);
function recompute() {{
  const candidates = parseCandidates();
  const events = [...(current.events || [])].sort((a, b) => a.ts_ms - b.ts_ms);
  const rows = current.snapshots || [];
  const pressures = Object.fromEntries(motiveKeys.map(key => [key, 0]));
  const recomputed = [];
  let eventIndex = 0;
  let recentEvents = [];
  let recentRejectionUntil = 0;
  let previousMs = rows.length ? rows[0].ts_ms : 0;
  for (const source of rows) {{
    const tsMs = source.ts_ms;
    const elapsedSec = Math.max(0, (tsMs - previousMs) / 1000);
    previousMs = tsMs;
    while (eventIndex < events.length && events[eventIndex].ts_ms <= tsMs) {{
      const event = events[eventIndex];
      recentEvents.push(event);
      if (event.lane === "main" && stopKeywords.some(word => String(event.text || "").includes(word))) {{
        recentRejectionUntil = Math.max(recentRejectionUntil, event.ts_ms + 60000);
      }}
      eventIndex += 1;
    }}
    recentEvents = recentEvents.filter(event => tsMs - event.ts_ms <= 120000);
    const userSpeaking = recentEvents.some(event =>
      event.lane === "main" &&
      event.event === "final_transcript_received" &&
      0 <= tsMs - event.ts_ms &&
      tsMs - event.ts_ms <= 1800
    );
    const tomokoSpeaking = recentEvents.some(event =>
      event.lane === "main" &&
      ["speaking", "client_playing"].includes(String((event.payload || {{}}).playback_state || "")) &&
      0 <= tsMs - event.ts_ms &&
      tsMs - event.ts_ms <= 3000
    );
    const floorAvailable = !userSpeaking && !tomokoSpeaking;
    decayPressures(pressures, elapsedSec);
    const availableCandidates = candidates.filter(candidate => candidateAvailableAt(candidate, tsMs));
    const topCandidate = selectTopCandidate(availableCandidates);
    applyCandidatePressure(pressures, topCandidate);
    if (floorAvailable) {{
      pressures.attachment = clamp01(pressures.attachment + Number(params.silence_attachment_gain || 0) * elapsedSec);
    }}
    const score = computeScore(pressures, topCandidate, {{
      floorAvailable,
      userSpeaking,
      tomokoSpeaking,
      rejectionActive: tsMs < recentRejectionUntil
    }});
    const dominantMotive = motiveKeys.reduce((best, key) => pressures[key] > pressures[best] ? key : best, "curiosity");
    const row = Object.assign({{}}, source, {{
      user_speaking: userSpeaking,
      tomoko_speaking: tomokoSpeaking,
      floor_available: floorAvailable,
      candidate_count: availableCandidates.length,
      top_candidate_id: topCandidate ? topCandidate.id : null,
      top_candidate_source: topCandidate ? topCandidate.source : null,
      top_candidate_text: topCandidateText(topCandidate),
      curiosity_pressure: round4(pressures.curiosity),
      teasing_pressure: round4(pressures.teasing),
      attachment_pressure: round4(pressures.attachment),
      unspoken_pressure: round4(pressures.unspoken),
      speak_score: round4(score),
      score2: score,
      fire2: score >= Number(params.threshold || 0) && floorAvailable,
      dominant_motive: dominantMotive,
      recent_text: recentConversationText(recentEvents)
    }});
    row.prompt_preview = buildPromptPreview(row, topCandidate);
    recomputed.push(row);
  }}
  return recomputed;
}}
function parseCandidates() {{
  try {{
    const parsed = JSON.parse(document.getElementById("candidateJson").value || "[]");
    return Array.isArray(parsed) ? parsed : [];
  }} catch {{
    return current.candidates || [];
  }}
}}
function candidateAvailableAt(candidate, tsMs) {{
  return Number(candidate.created_at_ms || 0) <= tsMs &&
    Number(candidate.expires_at_ms || 0) > tsMs &&
    (candidate.spoken_at_ms == null || Number(candidate.spoken_at_ms) > tsMs) &&
    (candidate.dismissed_at_ms == null || Number(candidate.dismissed_at_ms) > tsMs);
}}
function selectTopCandidate(candidates) {{
  if (!candidates.length) return null;
  return candidates.reduce((best, candidate) => compareCandidate(candidate, best) > 0 ? candidate : best, candidates[0]);
}}
function compareCandidate(left, right) {{
  const leftReady = left.maturity >= 1 && left.generated_text != null ? 1 : 0;
  const rightReady = right.maturity >= 1 && right.generated_text != null ? 1 : 0;
  if (leftReady !== rightReady) return leftReady - rightReady;
  if (Boolean(left.urgent) !== Boolean(right.urgent)) return Boolean(left.urgent) ? 1 : -1;
  if (Number(left.priority || 0) !== Number(right.priority || 0)) return Number(left.priority || 0) - Number(right.priority || 0);
  return Number(right.created_at_ms || 0) - Number(left.created_at_ms || 0);
}}
function applyCandidatePressure(pressures, candidate) {{
  if (!candidate) return;
  const motive = candidateMotive(candidate);
  const strength = candidateMotiveStrength(candidate);
  const gain = Number(params[`${{motive}}_gain`] || 0);
  pressures[motive] = clamp01(pressures[motive] + strength * gain * 0.05);
  if (["dismissed", "expired", "spoken"].includes(String(candidate.lifecycle || ""))) {{
    pressures.unspoken = clamp01(pressures.unspoken + Number(params.unspoken_gain || 0) * 0.01);
  }}
}}
function computeScore(pressures, candidate, state) {{
  const priority = candidate ? Number(candidate.priority || 0) : 0;
  const intrusion = candidateIntrusionRisk(candidate);
  return clamp01(
    pressures.curiosity +
    pressures.teasing +
    pressures.attachment +
    pressures.unspoken +
    (state.floorAvailable ? Number(params.floor_weight || 0) : 0) +
    Number(params.freshness_weight || 0) * priority -
    Number(params.intrusion_weight || 0) * intrusion -
    (state.userSpeaking ? Number(params.user_speaking_penalty || 0) : 0) -
    (state.tomokoSpeaking ? Number(params.tomoko_speaking_penalty || 0) : 0) -
    (state.rejectionActive ? Number(params.rejection_penalty || 0) : 0)
  );
}}
function decayPressures(pressures, elapsedSec) {{
  if (elapsedSec <= 0) return;
  const factor = Math.exp(-elapsedSec / Math.max(Number(params.decay_sec || 1), 1));
  for (const key of motiveKeys) pressures[key] = clamp01(pressures[key] * factor);
}}
function candidateMotive(candidate) {{
  const motive = (candidate.metadata_json || {{}}).motive;
  if (motiveKeys.includes(motive)) return motive;
  for (const tag of candidate.context_tags || []) {{
    const text = String(tag);
    if (!text.startsWith("motive:")) continue;
    const tagged = text.slice("motive:".length);
    if (motiveKeys.includes(tagged)) return tagged;
  }}
  if (["world_observation", "observation", "time_based"].includes(String(candidate.source || ""))) return "curiosity";
  if (["diary", "resume_unspoken"].includes(String(candidate.source || ""))) return "unspoken";
  return "attachment";
}}
function candidateMotiveStrength(candidate) {{
  const raw = (candidate.metadata_json || {{}}).motive_strength;
  if (typeof raw === "number") return clamp01(raw);
  return clamp01(Number(candidate.priority || 0) + (candidate.urgent ? 0.12 : 0));
}}
function candidateIntrusionRisk(candidate) {{
  if (!candidate) return 0;
  const raw = (candidate.metadata_json || {{}}).intrusion_risk;
  if (typeof raw === "number") return clamp01(raw);
  for (const tag of candidate.context_tags || []) {{
    const text = String(tag);
    if (!text.startsWith("intrusion_risk:")) continue;
    const parsed = Number(text.slice("intrusion_risk:".length));
    return Number.isFinite(parsed) ? clamp01(parsed) : 0;
  }}
  return 0.15;
}}
function topCandidateText(candidate) {{
  if (!candidate) return null;
  return candidate.generated_text || candidate.seed || null;
}}
function recentConversationText(events) {{
  return events
    .slice(-6)
    .filter(event => event.lane === "main" && event.text)
    .map(event => `${{event.event}}: ${{event.text}}`)
    .join("\\n");
}}
function buildPromptPreview(row, candidate) {{
  const candidateLine = topCandidateText(candidate) || "候補なし";
  const recent = row.recent_text || "近くの会話ログなし";
  const directive = motiveDirective(row.dominant_motive);
  return [
    "## INITIATIVE MOTIVATION SNAPSHOT",
    `dominant_motive: ${{row.dominant_motive}}`,
    `speak_score: ${{round4(row.score2)}}`,
    `floor_available: ${{row.floor_available}}`,
    "",
    "## INTEREST / CANDIDATE",
    candidateLine,
    "",
    "## NEARBY CONVERSATION",
    recent,
    "",
    "## MOTIVE DIRECTIVE",
    directive,
    "",
    "## OUTPUT CONTRACT",
    "横槍または自発発話として、一言だけ。長く説明せず、相手が返しやすい形にする。"
  ].join("\\n");
}}
function motiveDirective(motive) {{
  if (motive === "teasing") return "Tomoko は今、少しだけ「ちょっとちょっかいをかけたい」気持ちがある。\\nただし相手の作業を邪魔しすぎないように、短く軽い一言にする。";
  if (motive === "curiosity") return "Tomoko は今、画面や会話で起きていることが気になっている。\\n詰問ではなく、ふと気になった感じで短く聞く。";
  if (motive === "attachment") return "Tomoko は少し構ってほしい気持ちがある。\\n重くならず、相手の反応を待てる短い声かけにする。";
  return "Tomoko は言いそびれたことが少し残っている。\\n会話の流れを壊さないように、短く補足する。";
}}
function clamp01(value) {{
  return Math.max(0, Math.min(1, Number(value || 0)));
}}
function round4(value) {{
  return Math.round(Number(value || 0) * 10000) / 10000;
}}
function redraw() {{
  const rows = recompute();
  drawChart(rows);
  renderTimeline(rows);
  document.getElementById("summary").textContent = JSON.stringify({{
    session: original.sessions ? original.sessions[Number(sessionSelect.value || "0")].session_id : null,
    snapshots: rows.length,
    fire_count: rows.filter(r => r.fire2).length,
    threshold: params.threshold
  }}, null, 2);
}}
function drawChart(rows) {{
  const canvas = document.getElementById("chart");
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const pad = 36, w = canvas.width - pad * 2, h = canvas.height - pad * 2;
  ctx.strokeStyle = "#ddd"; ctx.beginPath(); ctx.moveTo(pad, pad); ctx.lineTo(pad, pad+h); ctx.lineTo(pad+w, pad+h); ctx.stroke();
  const keys = [["curiosity_pressure","#2673b8"],["teasing_pressure","#c45a1a"],["attachment_pressure","#6a9d35"],["unspoken_pressure","#7f5ab6"],["score2","#111"]];
  keys.forEach(([key, color]) => {{
    ctx.strokeStyle = color; ctx.beginPath();
    rows.forEach((r, i) => {{
      const x = pad + (i / Math.max(1, rows.length - 1)) * w;
      const y = pad + h - Math.max(0, Math.min(1, r[key])) * h;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }});
    ctx.stroke();
  }});
  ctx.strokeStyle = "#d04a02"; ctx.setLineDash([4,4]);
  const ty = pad + h - params.threshold * h;
  ctx.beginPath(); ctx.moveTo(pad, ty); ctx.lineTo(pad+w, ty); ctx.stroke(); ctx.setLineDash([]);
  rows.forEach((r, i) => {{
    if (!r.fire2) return;
    const x = pad + (i / Math.max(1, rows.length - 1)) * w;
    ctx.fillStyle = "#d04a02"; ctx.fillRect(x - 1, pad, 2, h);
  }});
}}
function renderTimeline(rows) {{
  const root = document.getElementById("timeline");
  root.innerHTML = "";
  const timelineItems = [
    ...(current.events || []).map(event => ({{ kind: "event", ts_ms: event.ts_ms, relative_sec: event.relative_sec, event }})),
    ...rows.filter(row => row.fire2).map(row => ({{ kind: "fire", ts_ms: row.ts_ms, relative_sec: row.relative_sec, row }}))
  ].sort((left, right) => (left.ts_ms - right.ts_ms) || (left.kind === "event" ? -1 : 1));
  timelineItems.slice(0, 500).forEach(item => {{
    const div = document.createElement("div");
    if (item.kind === "event") {{
      const e = item.event;
      div.className = "event";
      div.textContent = `+${{e.relative_sec}}s ${{e.lane}}/${{e.event}} ${{e.text || ""}}`;
    }} else {{
      const r = item.row;
      div.className = "event fire";
      div.textContent = `FIRE? +${{r.relative_sec}}s score=${{r.score2.toFixed(3)}} motive=${{r.dominant_motive}} ${{r.top_candidate_text || ""}}`;
      div.onclick = () => document.getElementById("preview").textContent = r.prompt_preview;
    }}
    root.append(div);
  }});
}}
setCandidateJson();
redraw();
</script>
</body>
</html>
"""


def event_to_json(event: TimelineEvent, start_ms: int) -> dict[str, Any]:
    return {
        "ts_ms": event.ts_ms,
        "relative_sec": round((event.ts_ms - start_ms) / 1000.0, 3),
        "lane": event.lane,
        "event": event.event,
        "text": event.text,
        "payload": event.payload,
    }


def summarize_snapshots(
    snapshots: list[dict[str, Any]],
    markers: list[dict[str, Any]],
    thresholds: tuple[float, ...],
) -> dict[str, Any]:
    first_by_threshold: dict[str, float | None] = {}
    for threshold in thresholds:
        key = f"{threshold:.2f}"
        hit = next(
            (snapshot["relative_sec"] for snapshot in snapshots if snapshot["would_fire"][key]),
            None,
        )
        first_by_threshold[key] = hit
    return {
        "snapshot_count": len(snapshots),
        "fire_marker_count": len(markers),
        "first_fire_sec_by_threshold": first_by_threshold,
        "max_score": max((snapshot["speak_score"] for snapshot in snapshots), default=0.0),
    }


def recent_conversation_text(events: list[TimelineEvent]) -> str:
    lines = []
    for event in events[-6:]:
        if event.lane == "main" and event.text:
            lines.append(f"{event.event}: {event.text}")
    return "\n".join(lines)


def candidate_text(candidate: CandidateView | None) -> str | None:
    if candidate is None:
        return None
    return candidate.generated_text or candidate.seed


def candidate_motive(candidate: CandidateView) -> str:
    raw = candidate.metadata_json.get("motive")
    if isinstance(raw, str) and raw in MOTIVES:
        return raw
    for tag in candidate.context_tags:
        if tag.startswith("motive:"):
            motive = tag.removeprefix("motive:")
            if motive in MOTIVES:
                return motive
    if candidate.source in {"world_observation", "observation", "time_based"}:
        return "curiosity"
    if candidate.source in {"diary", "resume_unspoken"}:
        return "unspoken"
    return "attachment"


def candidate_motive_strength(candidate: CandidateView) -> float:
    raw = candidate.metadata_json.get("motive_strength")
    if isinstance(raw, int | float):
        return clamp01(float(raw))
    return clamp01(candidate.priority + (0.12 if candidate.urgent else 0.0))


def candidate_intrusion_risk(candidate: CandidateView | None) -> float:
    if candidate is None:
        return 0.0
    raw = candidate.metadata_json.get("intrusion_risk")
    if isinstance(raw, int | float):
        return clamp01(float(raw))
    for tag in candidate.context_tags:
        if tag.startswith("intrusion_risk:"):
            try:
                return clamp01(float(tag.removeprefix("intrusion_risk:")))
            except ValueError:
                return 0.0
    return 0.15


def decay_pressures(
    pressures: dict[str, float],
    elapsed_sec: float,
    decay_sec: float,
) -> None:
    if elapsed_sec <= 0:
        return
    factor = math.exp(-elapsed_sec / max(decay_sec, 1.0))
    for key, value in list(pressures.items()):
        pressures[key] = clamp01(value * factor)


def parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if value is None:
        return datetime.fromtimestamp(0, UTC)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_time_ms(value: Any) -> int:
    return int(parse_datetime(value).timestamp() * 1000)


def parse_optional_time_ms(value: Any) -> int | None:
    if value is None:
        return None
    return parse_time_ms(value)


def now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_html(path: Path, simulation: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(simulation), encoding="utf-8")


def json_dumps_compact(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
