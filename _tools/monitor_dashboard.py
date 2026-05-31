from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from _tools.monitor_snapshot import build_monitor_snapshot  # noqa: E402


class MonitorRequestHandler(BaseHTTPRequestHandler):
    server: MonitorHTTPServer

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            self._send_html(_dashboard_html())
            return
        if path == "/api/snapshot":
            snapshot = build_monitor_snapshot(
                server_log_path=self.server.server_log_path,
                backend_trace_path=self.server.backend_trace_path,
                system_metrics_path=self.server.system_metrics_path,
                config_path=self.server.config_path,
                log_tail_lines=self.server.log_tail_lines,
            )
            self._send_json(snapshot)
            return
        self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_html(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_json(self, payload: dict) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class MonitorHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        server_log_path: Path,
        backend_trace_path: Path,
        system_metrics_path: Path,
        config_path: Path | None,
        log_tail_lines: int,
    ) -> None:
        super().__init__(server_address, MonitorRequestHandler)
        self.server_log_path = server_log_path
        self.backend_trace_path = backend_trace_path
        self.system_metrics_path = system_metrics_path
        self.config_path = config_path
        self.log_tail_lines = log_tail_lines


def run_monitor(
    *,
    host: str,
    port: int,
    server_log_path: Path,
    backend_trace_path: Path,
    system_metrics_path: Path,
    config_path: Path | None,
    log_tail_lines: int,
) -> None:
    httpd = MonitorHTTPServer(
        (host, port),
        server_log_path=server_log_path,
        backend_trace_path=backend_trace_path,
        system_metrics_path=system_metrics_path,
        config_path=config_path,
        log_tail_lines=log_tail_lines,
    )
    print(f"Tomoko monitor listening on http://{host}:{port}")
    httpd.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a read-only Tomoko monitor dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--server-log", type=Path, default=Path("logs/server-debug.log"))
    parser.add_argument("--backend-trace", type=Path, default=Path("logs/backend-trace.jsonl"))
    parser.add_argument("--system-metrics", type=Path, default=Path("logs/system-metrics.jsonl"))
    parser.add_argument("--config", type=Path, default=Path("config/central_realtime.toml"))
    parser.add_argument("--log-tail-lines", type=int, default=4000)
    args = parser.parse_args(argv)
    run_monitor(
        host=args.host,
        port=args.port,
        server_log_path=args.server_log,
        backend_trace_path=args.backend_trace,
        system_metrics_path=args.system_metrics,
        config_path=args.config,
        log_tail_lines=args.log_tail_lines,
    )
    return 0


def _dashboard_html() -> str:
    return """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tomoko Monitor</title>
<style>
:root {
  color-scheme: dark;
  --bg: #101318;
  --panel: #181d25;
  --line: #303744;
  --text: #edf1f7;
  --muted: #a7b0bf;
  --accent: #58b7d8;
  --warn: #f0c96b;
  --bad: #ff6b6b;
  --ok: #78d286;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
header {
  position: sticky;
  top: 0;
  background: rgba(16, 19, 24, 0.96);
  border-bottom: 1px solid var(--line);
  padding: 14px 18px;
}
h1 { margin: 0 0 4px; font-size: 18px; }
.muted { color: var(--muted); }
main { padding: 14px; max-width: 1280px; margin: 0 auto; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 10px; }
.card {
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--panel);
  padding: 12px;
}
.card strong { display: block; font-size: 24px; margin-top: 4px; }
.depth-fast { color: var(--ok); }
.depth-normal { color: var(--accent); }
.depth-deep { color: var(--warn); }
.depth-reflective { color: var(--bad); }
.timeline { margin-top: 14px; border: 1px solid var(--line); border-radius: 7px; overflow: hidden; }
.row {
  display: grid;
  grid-template-columns: 92px 154px 1fr;
  gap: 10px;
  padding: 8px 10px;
  border-bottom: 1px solid rgba(255,255,255,0.06);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.row:nth-child(even) { background: #151a22; }
.kind-error { color: var(--bad); }
.kind-warning { color: var(--warn); }
.kind-context, .kind-conversation_prompt { color: var(--accent); }
.kind-initiative, .kind-turn_taking { color: var(--warn); }
.message { overflow-wrap: anywhere; white-space: pre-wrap; }
@media (max-width: 760px) {
  .row { grid-template-columns: 82px 1fr; }
  .logger { display: none; }
}
</style>
</head>
<body>
<header>
  <h1>Tomoko Monitor</h1>
  <div class="muted" id="meta">loading...</div>
</header>
<main>
  <div class="grid" id="cards"></div>
  <section class="timeline" id="timeline"></section>
</main>
<script>
async function refresh() {
  const response = await fetch("/api/snapshot", { cache: "no-store" });
  const data = await response.json();
  render(data);
}

function render(data) {
  document.getElementById("meta").textContent =
    `generated ${data.generated_at} / server ${data.sources.server_log}`;
  const context = data.context.latest;
  const db = data.database;
  const recentCalls = data.backend_trace.recent_calls || [];
  const conversationCalls = recentCalls.filter((call) => call.role === "conversation");
  const promptCount = data.categories.conversation_prompt || 0;
  const gpu = data.system_metrics.latest;
  const cards = [
    ["Context depth", context ? context.depth : "none", context ? `depth-${context.depth}` : ""],
    ["Context elapsed", context ? `${context.elapsed_ms} ms` : "-", ""],
    [
      "Timed out",
      context && context.timed_out ? "yes" : "no",
      context && context.timed_out ? "depth-reflective" : ""
    ],
    ["Conversation prompts", promptCount, ""],
    ["Backend calls", recentCalls.length, ""],
    ["Conversation calls", conversationCalls.length, ""],
    [
      "DB",
      db.available ? "connected" : "unavailable",
      db.available ? "depth-fast" : "depth-reflective"
    ],
    ["Active candidates", db.available ? db.utterance_candidates.active : "-", ""],
    [
      "GPU",
      gpu && gpu.available && gpu.gpu_active_percent !== null
        ? `${gpu.gpu_active_percent.toFixed(1)}%`
        : "unavailable",
      gpu && gpu.available ? "depth-normal" : "depth-reflective"
    ],
    [
      "GPU power",
      gpu && gpu.available && gpu.gpu_total_power_w !== null
        ? `${gpu.gpu_total_power_w.toFixed(2)} W`
        : "-",
      ""
    ],
    [
      "GPU freq",
      gpu && gpu.available && gpu.gpu_freq_mhz !== null
        ? `${gpu.gpu_freq_mhz.toFixed(0)} MHz`
        : "-",
      ""
    ],
    [
      "Thermal",
      gpu && gpu.available && gpu.thermal_state ? gpu.thermal_state : "-",
      gpu && gpu.thermal_state && gpu.thermal_state !== "Nominal" ? "depth-deep" : ""
    ],
  ];
  document.getElementById("cards").innerHTML = cards.map(([label, value, cls]) =>
    `<div class="card"><span class="muted">${escapeHtml(label)}</span>` +
    `<strong class="${cls}">${escapeHtml(value)}</strong></div>`
  ).join("");
  renderTimeline(data.timeline || []);
}

function renderTimeline(events) {
  const root = document.getElementById("timeline");
  if (!events.length) {
    root.innerHTML =
      `<div class="row"><span></span><span class="message muted">No events yet.</span></div>`;
    return;
  }
  root.innerHTML = events.slice(-80).reverse().map((event) => {
    const time = event.timestamp ? event.timestamp.slice(11) : "";
    return `<div class="row">` +
      `<span class="kind-${event.kind}">${escapeHtml(event.kind)}</span>` +
      `<span class="logger">${escapeHtml(shortLogger(event.logger))}</span>` +
      `<span class="message">${escapeHtml(time + " " + event.message)}</span>` +
      `</div>`;
  }).join("");
}

function shortLogger(logger) {
  if (!logger) return "";
  const parts = logger.split(".");
  return parts.slice(-2).join(".");
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }[char]));
}

refresh();
setInterval(refresh, 2500);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
