from __future__ import annotations

import argparse
import html
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

TIMESTAMP_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[,.]\d{3}) "
    r"(?P<level>[A-Z]+):(?P<logger>[^:]+):(?P<message>.*)$"
)
UVICORN_RE = re.compile(r"^(?P<level>[A-Z]+):\s+(?P<message>.*)$")
STARTED_PROCESS_RE = re.compile(r"Started server process \[(?P<pid>\d+)\]")
FINISHED_PROCESS_RE = re.compile(r"Finished server process \[(?P<pid>\d+)\]")

CATEGORY_ORDER = (
    "error",
    "warning",
    "startup",
    "reload",
    "session",
    "transcript",
    "participation",
    "conversation_prompt",
    "reply",
    "tts",
    "playback",
    "initiative",
    "turn_taking",
    "stt",
    "context",
    "memory",
    "backend",
    "db",
    "other",
)


@dataclass(frozen=True)
class ParsedLine:
    index: int
    run_index: int
    timestamp: str | None
    level: str
    logger: str
    message: str
    raw: str
    category: str
    density: int


@dataclass(frozen=True)
class ParsedRun:
    index: int
    pid: str | None
    start_line: int
    end_line: int | None
    started_at: str | None
    ended_at: str | None
    line_count: int
    category_counts: dict[str, int]


@dataclass(frozen=True)
class ParsedLog:
    source: str
    generated_at: str
    lines: list[ParsedLine]
    runs: list[ParsedRun]
    category_counts: dict[str, int]


def read_tail_lines(path: Path, max_lines: int | None) -> list[str]:
    lines = path.read_text(errors="replace").splitlines()
    if max_lines is None or max_lines <= 0:
        return lines
    return lines[-max_lines:]


def parse_log_lines(lines: list[str], *, source: str = "server-debug.log") -> ParsedLog:
    parsed_lines: list[ParsedLine] = []
    run_states: list[dict[str, Any]] = []
    current_run = -1

    for index, raw in enumerate(lines, start=1):
        parsed = _parse_raw_line(raw)
        message = parsed["message"]
        started = STARTED_PROCESS_RE.search(message)
        if started is not None:
            current_run += 1
            run_states.append(
                {
                    "index": current_run,
                    "pid": started.group("pid"),
                    "start_line": index,
                    "end_line": None,
                    "started_at": parsed["timestamp"],
                    "ended_at": None,
                    "line_count": 0,
                    "category_counts": Counter(),
                }
            )
        elif current_run < 0:
            current_run = 0
            run_states.append(
                {
                    "index": current_run,
                    "pid": None,
                    "start_line": index,
                    "end_line": None,
                    "started_at": parsed["timestamp"],
                    "ended_at": None,
                    "line_count": 0,
                    "category_counts": Counter(),
                }
            )

        category = classify_event(
            level=parsed["level"],
            logger=parsed["logger"],
            message=message,
            raw=raw,
        )
        parsed_line = ParsedLine(
            index=index,
            run_index=current_run,
            timestamp=parsed["timestamp"],
            level=parsed["level"],
            logger=parsed["logger"],
            message=message,
            raw=raw,
            category=category,
            density=event_density(
                category=category,
                level=parsed["level"],
                message=message,
            ),
        )
        parsed_lines.append(parsed_line)

        run_state = run_states[current_run]
        run_state["line_count"] += 1
        run_state["category_counts"][category] += 1
        finished = FINISHED_PROCESS_RE.search(message)
        if finished is not None:
            run_state["end_line"] = index
            run_state["ended_at"] = parsed["timestamp"]
        elif message == "Application startup complete." and run_state["started_at"] is None:
            run_state["started_at"] = parsed["timestamp"]

    runs = [
        ParsedRun(
            index=run["index"],
            pid=run["pid"],
            start_line=run["start_line"],
            end_line=run["end_line"],
            started_at=run["started_at"],
            ended_at=run["ended_at"],
            line_count=run["line_count"],
            category_counts=dict(run["category_counts"]),
        )
        for run in run_states
    ]
    return ParsedLog(
        source=source,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        lines=parsed_lines,
        runs=runs,
        category_counts=dict(Counter(line.category for line in parsed_lines)),
    )


def classify_event(*, level: str, logger: str, message: str, raw: str) -> str:
    haystack = f"{logger} {message} {raw}".casefold()
    if level in {"ERROR", "CRITICAL"} or "traceback" in haystack:
        return "error"
    if "watchfiles detected changes" in haystack or "reloading" in haystack:
        return "reload"
    if level == "WARNING" or raw.startswith("WARNING:"):
        return "warning"
    if "started server process" in haystack or "application startup" in haystack:
        return "startup"
    if "shutting down" in haystack or "finished server process" in haystack:
        return "startup"
    if "turn_taking" in haystack or "turn-taking" in haystack:
        return "turn_taking"
    if "thinkfastmode llm_prompt" in haystack:
        return "conversation_prompt"
    if "server.gateway.thinking.fast" in haystack and "llm_prompt" in haystack:
        return "conversation_prompt"
    if "initiative" in haystack or "arrival" in haystack or "candidate" in haystack:
        return "initiative"
    if "transcript" in haystack or "stt finalized" in haystack:
        return "transcript"
    if "participation" in haystack or "wake_word" in haystack or "attention" in haystack:
        return "participation"
    if "reply" in haystack or "llm_delta" in haystack or "tomoko turn" in haystack:
        return "reply"
    if "playback" in haystack or "audio_start" in haystack or "audio_end" in haystack:
        return "playback"
    if "tts" in haystack or "voicevox" in haystack or "synthesize" in haystack:
        return "tts"
    if "stt" in haystack or "apple_speech" in haystack or "whisper" in haystack:
        return "stt"
    if "contextsnapshot" in haystack or "context snapshot" in haystack:
        return "context"
    if "short memory" in haystack or "memory_extraction" in haystack:
        return "memory"
    if "backend" in haystack or "lm studio" in haystack or "inference" in haystack:
        return "backend"
    if "postgres" in haystack or "conversation_log" in haystack or "db " in haystack:
        return "db"
    if "tomorosession" in haystack or "transition" in haystack or "state=" in haystack:
        return "session"
    return "other"


def event_density(*, category: str, level: str, message: str) -> int:
    if category in {"error", "warning", "startup", "reload"}:
        return 1
    if category in {
        "transcript",
        "participation",
        "conversation_prompt",
        "reply",
        "initiative",
        "turn_taking",
        "session",
    }:
        return 1
    if category in {"tts", "playback", "stt", "context", "memory", "backend", "db"}:
        return 2
    if level == "INFO" and "warm-up" in message:
        return 2
    return 3


def write_html_report(parsed: ParsedLog, output_path: Path) -> None:
    payload = json.dumps(_parsed_log_to_json(parsed), ensure_ascii=False).replace(
        "</",
        "<\\/",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_html_document(payload), encoding="utf-8")


def _parse_raw_line(raw: str) -> dict[str, str | None]:
    timestamp_match = TIMESTAMP_RE.match(raw)
    if timestamp_match is not None:
        return {
            "timestamp": timestamp_match.group("timestamp"),
            "level": timestamp_match.group("level"),
            "logger": timestamp_match.group("logger"),
            "message": timestamp_match.group("message"),
        }
    uvicorn_match = UVICORN_RE.match(raw)
    if uvicorn_match is not None:
        return {
            "timestamp": None,
            "level": uvicorn_match.group("level"),
            "logger": "uvicorn",
            "message": uvicorn_match.group("message").strip(),
        }
    return {
        "timestamp": None,
        "level": "RAW",
        "logger": "raw",
        "message": raw,
    }


def _parsed_log_to_json(parsed: ParsedLog) -> dict[str, Any]:
    return {
        "source": parsed.source,
        "generated_at": parsed.generated_at,
        "runs": [asdict(run) for run in parsed.runs],
        "lines": [asdict(line) for line in parsed.lines],
        "category_counts": parsed.category_counts,
        "category_order": CATEGORY_ORDER,
    }


def _html_document(payload: str) -> str:
    escaped_title = html.escape("Tomoko server-debug log report")
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escaped_title}</title>
<style>
:root {{
  color-scheme: dark;
  --bg: #111318;
  --panel: #181b22;
  --panel2: #20242d;
  --text: #eceff4;
  --muted: #aab2c0;
  --line: #343a46;
  --accent: #55b4d4;
  --bad: #ff6b6b;
  --warn: #f3c969;
  --ok: #82d173;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}}
header {{
  position: sticky;
  top: 0;
  z-index: 2;
  background: rgba(17, 19, 24, 0.96);
  border-bottom: 1px solid var(--line);
  padding: 14px 18px;
}}
h1 {{ margin: 0 0 8px; font-size: 18px; }}
.meta, .muted {{ color: var(--muted); }}
.layout {{ display: grid; grid-template-columns: 320px 1fr; min-height: calc(100vh - 74px); }}
aside {{
  border-right: 1px solid var(--line);
  padding: 14px;
  background: var(--panel);
  overflow: auto;
}}
main {{ padding: 14px; overflow: auto; }}
.section {{ margin-bottom: 16px; }}
.section h2 {{ font-size: 13px; margin: 0 0 8px; color: var(--muted); text-transform: uppercase; }}
.run {{
  width: 100%;
  border: 1px solid var(--line);
  background: var(--panel2);
  color: var(--text);
  text-align: left;
  padding: 10px;
  margin: 0 0 8px;
  border-radius: 6px;
  cursor: pointer;
}}
.run.active {{ border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent) inset; }}
.runCounts {{ margin-top: 6px; display: flex; flex-wrap: wrap; gap: 5px; }}
.runCount {{ color: var(--muted); font-size: 12px; }}
.toggle {{ display: block; margin: 0 0 8px; color: var(--muted); }}
.toggle input {{ margin-right: 6px; }}
.chips {{ display: flex; flex-wrap: wrap; gap: 6px; }}
.chip {{
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 5px 8px;
  background: var(--panel2);
  cursor: pointer;
  user-select: none;
}}
.chip input {{ margin-right: 5px; }}
input[type="search"] {{
  width: 100%;
  padding: 8px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--panel2);
  color: var(--text);
}}
input[type="range"] {{ width: 100%; }}
.summary {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}}
.card {{
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--panel);
  padding: 10px;
}}
.card strong {{ display: block; font-size: 20px; }}
.timeline {{
  border: 1px solid var(--line);
  border-radius: 6px;
  overflow: hidden;
}}
.row {{
  display: grid;
  grid-template-columns: 74px 88px 130px 1fr;
  gap: 10px;
  padding: 7px 10px;
  border-bottom: 1px solid rgba(255,255,255,0.06);
  background: #151820;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  white-space: pre-wrap;
}}
.row:nth-child(even) {{ background: #181c25; }}
.level-WARNING, .category-warning {{ color: var(--warn); }}
.level-ERROR, .level-CRITICAL, .category-error {{ color: var(--bad); }}
.category-startup {{ color: var(--ok); }}
.category-reply, .category-transcript, .category-conversation_prompt {{ color: var(--accent); }}
.category-other {{ color: var(--muted); }}
.message {{ overflow-wrap: anywhere; }}
@media (max-width: 860px) {{
  .layout {{ grid-template-columns: 1fr; }}
  aside {{ border-right: 0; border-bottom: 1px solid var(--line); }}
  .row {{ grid-template-columns: 64px 72px 1fr; }}
  .logger {{ display: none; }}
}}
</style>
</head>
<body>
<header>
  <h1>Tomoko server-debug log report</h1>
  <div class="meta" id="reportMeta"></div>
</header>
<div class="layout">
  <aside>
    <div class="section">
      <h2>Runs</h2>
      <label class="toggle">
        <input id="matchingRunsOnly" type="checkbox" checked>
        runs with visible lines
      </label>
      <div id="runList"></div>
    </div>
    <div class="section">
      <h2>Density</h2>
      <input id="densitySlider" type="range" min="1" max="3" value="2">
      <div class="muted">show density &lt;= <span id="densityValue">2</span></div>
    </div>
    <div class="section">
      <h2>Search</h2>
      <input id="searchBox" type="search" placeholder="logger / message">
    </div>
    <div class="section">
      <h2>Events</h2>
      <div id="categoryList" class="chips"></div>
    </div>
  </aside>
  <main>
    <div class="summary" id="summary"></div>
    <div class="timeline" id="timeline"></div>
  </main>
</div>
<script id="log-data" type="application/json">{payload}</script>
<script>
const data = JSON.parse(document.getElementById("log-data").textContent);
const state = {{
  runIndex: initialRunIndex(),
  density: 2,
  categories: new Set(data.category_order),
  search: "",
  matchingRunsOnly: true
}};

function initialRunIndex() {{
  const interesting = [
    "transcript",
    "conversation_prompt",
    "reply",
    "initiative",
    "turn_taking"
  ];
  for (let i = data.runs.length - 1; i >= 0; i -= 1) {{
    const run = data.runs[i];
    if (interesting.some((category) => (run.category_counts[category] || 0) > 0)) {{
      return run.index;
    }}
  }}
  return data.runs.length ? data.runs[data.runs.length - 1].index : 0;
}}

function init() {{
  document.getElementById("reportMeta").textContent =
    `${{data.source}} / generated ${{data.generated_at}} / ${{data.lines.length}} lines`;
  renderRuns();
  renderCategories();
  bindControls();
  render();
}}

function bindControls() {{
  const slider = document.getElementById("densitySlider");
  slider.addEventListener("input", () => {{
    state.density = Number(slider.value);
    document.getElementById("densityValue").textContent = String(state.density);
    selectNewestMatchingRunIfNeeded();
    renderRuns();
    render();
  }});
  document.getElementById("searchBox").addEventListener("input", (event) => {{
    state.search = event.target.value.toLowerCase();
    selectNewestMatchingRunIfNeeded();
    renderRuns();
    render();
  }});
  document.getElementById("matchingRunsOnly").addEventListener("change", (event) => {{
    state.matchingRunsOnly = event.target.checked;
    renderRuns();
  }});
}}

function renderRuns() {{
  const root = document.getElementById("runList");
  root.innerHTML = "";
  const runs = state.matchingRunsOnly
    ? data.runs.filter((run) => runHasVisibleLine(run.index))
    : data.runs;
  runs.forEach((run) => {{
    const button = document.createElement("button");
    button.className = "run" + (run.index === state.runIndex ? " active" : "");
    const title = `Run ${{run.index + 1}}${{run.pid ? " / pid " + run.pid : ""}}`;
    const range = `${{run.start_line}}-${{run.end_line || "..."}}`;
    button.innerHTML = `<strong>${{escapeHtml(title)}}</strong><br>` +
      `<span class="muted">lines ${{range}} / ${{run.line_count}}</span>` +
      runCountHtml(run);
    button.addEventListener("click", () => {{
      state.runIndex = run.index;
      renderRuns();
      render();
    }});
    root.appendChild(button);
  }});
  if (!runs.length) {{
    root.innerHTML = `<div class="muted">No runs match the current filters.</div>`;
  }}
}}

function runCountHtml(run) {{
  const keys = [
    "transcript",
    "conversation_prompt",
    "reply",
    "initiative",
    "turn_taking",
    "error",
    "warning"
  ];
  const chips = keys
    .filter((key) => (run.category_counts[key] || 0) > 0)
    .map((key) => `<span class="runCount category-${{key}}">` +
      `${{key}} ${{run.category_counts[key]}}</span>`);
  return chips.length ? `<div class="runCounts">${{chips.join("")}}</div>` : "";
}}

function renderCategories() {{
  const root = document.getElementById("categoryList");
  root.innerHTML = "";
  data.category_order.forEach((category) => {{
    const label = document.createElement("label");
    label.className = "chip category-" + category;
    const count = data.category_counts[category] || 0;
    label.innerHTML =
      `<input type="checkbox" checked value="${{category}}">${{category}} ${{count}}`;
    label.querySelector("input").addEventListener("change", (event) => {{
      if (event.target.checked) state.categories.add(category);
      else state.categories.delete(category);
      selectNewestMatchingRunIfNeeded();
      renderRuns();
      render();
    }});
    root.appendChild(label);
  }});
}}

function render() {{
  const filtered = data.lines.filter((line) => lineMatchesFilters(line, true));
  renderSummary(filtered);
  renderTimeline(filtered);
}}

function lineMatchesFilters(line, includeRun) {{
  if (includeRun && line.run_index !== state.runIndex) return false;
  if (line.density > state.density) return false;
  if (!state.categories.has(line.category)) return false;
  if (state.search) {{
    const haystack = `${{line.logger}} ${{line.message}} ${{line.raw}}`.toLowerCase();
    if (!haystack.includes(state.search)) return false;
  }}
  return true;
}}

function runHasVisibleLine(runIndex) {{
  return data.lines.some((line) => line.run_index === runIndex && lineMatchesFilters(line, false));
}}

function selectNewestMatchingRunIfNeeded() {{
  if (runHasVisibleLine(state.runIndex)) return;
  for (let i = data.runs.length - 1; i >= 0; i -= 1) {{
    if (runHasVisibleLine(data.runs[i].index)) {{
      state.runIndex = data.runs[i].index;
      return;
    }}
  }}
}}

function renderSummary(lines) {{
  const counts = {{}};
  lines.forEach((line) => counts[line.category] = (counts[line.category] || 0) + 1);
  const run = data.runs.find((item) => item.index === state.runIndex);
  const cards = [
    ["Visible", lines.length],
    ["Run lines", run ? run.line_count : 0],
    ["Errors", counts.error || 0],
    ["Warnings", counts.warning || 0],
    ["Transcript", counts.transcript || 0],
    ["Prompt", counts.conversation_prompt || 0],
    ["Reply", counts.reply || 0],
  ];
  document.getElementById("summary").innerHTML = cards.map(([label, value]) =>
    `<div class="card"><span class="muted">${{label}}</span><strong>${{value}}</strong></div>`
  ).join("");
}}

function renderTimeline(lines) {{
  const root = document.getElementById("timeline");
  if (!lines.length) {{
    root.innerHTML = `<div class="row"><span></span><span></span><span></span>` +
      `<span class="message muted">No matching lines in this run. ` +
      `Adjust filters or disable runs-with-visible-lines.</span></div>`;
    return;
  }}
  root.innerHTML = lines.map((line) => {{
    const time = line.timestamp ? line.timestamp.slice(11) : "#" + line.index;
    return `<div class="row">` +
      `<span class="category-${{line.category}}">${{escapeHtml(line.category)}}</span>` +
      `<span class="level-${{line.level}}">${{escapeHtml(line.level)}}</span>` +
      `<span class="logger">${{escapeHtml(shortLogger(line.logger))}}</span>` +
      `<span class="message">${{escapeHtml(time + " " + line.message)}}</span>` +
      `</div>`;
  }}).join("");
}}

function shortLogger(logger) {{
  if (!logger) return "";
  const parts = logger.split(".");
  return parts.slice(-2).join(".");
}}

function escapeHtml(value) {{
  return String(value).replace(/[&<>"']/g, (char) => ({{
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }}[char]));
}}

init();
</script>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate an HTML report for logs/server-debug.log."
    )
    parser.add_argument("--input", type=Path, default=Path("logs/server-debug.log"))
    parser.add_argument("--output", type=Path, default=Path("logs/server-debug-report.html"))
    parser.add_argument(
        "--max-lines",
        type=int,
        default=50000,
        help="Read only the latest N lines. Use 0 to read the whole file.",
    )
    args = parser.parse_args(argv)

    lines = read_tail_lines(args.input, None if args.max_lines == 0 else args.max_lines)
    parsed = parse_log_lines(lines, source=str(args.input))
    write_html_report(parsed, args.output)
    print(f"wrote {args.output} ({len(parsed.runs)} runs, {len(parsed.lines)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
