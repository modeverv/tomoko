"""
Turn-taking v2 HTMLタイムラインビジュアライザー

各ユーザー発話ターンについて:
- メイン推論が始まったタイミング (final_transcript_received / provisional_inference_start)
- シャドウワーカーが would_start_inference=True を出したタイミング

を横軸＝時間、縦軸＝発話ごとのスイムレーンで描画する。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def generate_html_timeline(
    session_id: str,
    main_recs: list[dict[str, Any]],
    v2_recs: list[dict[str, Any]],
) -> str:
    """
    指定 session_id の発話ターンを可視化した HTML 文字列を返す。

    ■ メインレーン (main):
      - final_transcript_received … 従来の発話終了→推論開始判断
      - provisional_inference_start … シャドウワーカーのシグナルを受けてメインが
                                      早期推論を開始した瞬間
    ■ シャドウレーン (v2_shadow):
      - would_start_inference=True のレコード → 「推論促進シグナル」
      - それ以外の speech_decision_score レコード → スコア推移
    """
    # --- フィルタリング ---
    main_filtered = [r for r in main_recs if r.get("conversation_session_id") == session_id]
    v2_filtered = [r for r in v2_recs if r.get("conversation_session_id") == session_id]

    # --- ターン単位に集約 ---
    turns: dict[str, dict[str, Any]] = {}
    for r in main_filtered:
        tid = r.get("turn_id")
        if not tid:
            continue
        turns.setdefault(tid, {"main_events": [], "v2_events": []})
        turns[tid]["main_events"].append(r)

    for r in v2_filtered:
        tid = r.get("turn_id")
        if not tid:
            continue
        turns.setdefault(tid, {"main_events": [], "v2_events": []})
        turns[tid]["v2_events"].append(r)

    # main_final のある turn だけ残す
    valid_turns = {
        tid: data
        for tid, data in turns.items()
        if any(e.get("event") == "final_transcript_received" for e in data["main_events"])
    }
    if not valid_turns:
        # session_id に final_transcript がなければ v2 のみのターンも可視化
        valid_turns = turns

    # ターンを時刻昇順でソート（final_transcript_received の ts_ms、なければ最小 ts_ms）
    def turn_start_ts(data: dict) -> int:
        final_evs = [e for e in data["main_events"] if e.get("event") == "final_transcript_received"]
        if final_evs:
            return min(e["ts_ms"] for e in final_evs)
        all_evs = data["main_events"] + data["v2_events"]
        return min((e["ts_ms"] for e in all_evs), default=0)

    sorted_turns = sorted(valid_turns.items(), key=lambda kv: turn_start_ts(kv[1]))

    if not sorted_turns:
        return _empty_html(session_id)

    # グローバル時刻範囲
    all_ts = [e["ts_ms"] for _, data in sorted_turns for e in data["main_events"] + data["v2_events"]]
    global_start = min(all_ts)
    global_end = max(all_ts)
    total_span = max(global_end - global_start, 1)

    # 1発話あたりの行高さ (px)
    ROW_H = 110
    HEADER_H = 60
    TIMELINE_W = 900  # タイムバー幅 (px)
    LABEL_W = 180     # 左ラベル幅 (px)
    SVG_H = HEADER_H + len(sorted_turns) * ROW_H + 40

    def ts_to_x(ts_ms: int) -> float:
        return LABEL_W + (ts_ms - global_start) / total_span * TIMELINE_W

    # SVG 要素を組み立て
    svg_parts: list[str] = []

    # 目盛り線（1秒ごと）
    span_sec = total_span / 1000
    tick_interval = max(1, int(span_sec / 10))  # 最大10本
    t = 0
    while t * 1000 <= total_span:
        x = LABEL_W + t * 1000 / total_span * TIMELINE_W
        svg_parts.append(
            f'<line x1="{x:.1f}" y1="{HEADER_H}" x2="{x:.1f}" y2="{SVG_H - 20}"'
            f' stroke="#334155" stroke-width="1" stroke-dasharray="4,4"/>'
        )
        svg_parts.append(
            f'<text x="{x:.1f}" y="{HEADER_H - 6}" fill="#64748b" font-size="11"'
            f' text-anchor="middle">+{t}s</text>'
        )
        t += tick_interval

    # 各ターンの行
    outcome_colors = {
        "good_early_prepare": "#22d3ee",
        "too_early_wrong": "#f87171",
        "missed_opportunity": "#fb923c",
        "safe_wait": "#94a3b8",
        "dangerous_speak": "#f43f5e",
    }

    turn_data_json: list[dict] = []  # JS に渡すデータ

    for row_idx, (tid, data) in enumerate(sorted_turns):
        y_top = HEADER_H + row_idx * ROW_H
        y_center = y_top + ROW_H / 2
        y_main = y_top + ROW_H * 0.3
        y_shadow = y_top + ROW_H * 0.7

        # 行背景
        bg_color = "#0f172a" if row_idx % 2 == 0 else "#111827"
        svg_parts.append(
            f'<rect x="0" y="{y_top}" width="{LABEL_W + TIMELINE_W + 60}"'
            f' height="{ROW_H}" fill="{bg_color}"/>'
        )

        # 行境界線
        svg_parts.append(
            f'<line x1="0" y1="{y_top}" x2="{LABEL_W + TIMELINE_W + 60}" y2="{y_top}"'
            f' stroke="#1e293b" stroke-width="1"/>'
        )

        # ラベル
        main_finals = [e for e in data["main_events"] if e.get("event") == "final_transcript_received"]
        text_label = ""
        if main_finals:
            text_label = main_finals[0].get("text") or ""

        short_label = text_label[:18] + "…" if len(text_label) > 18 else text_label
        svg_parts.append(
            f'<text x="10" y="{y_top + 22}" fill="#cbd5e1" font-size="12" font-weight="bold">'
            f'Turn {row_idx + 1}</text>'
        )
        svg_parts.append(
            f'<text x="10" y="{y_top + 38}" fill="#94a3b8" font-size="10">'
            f'{_escape(short_label)}</text>'
        )

        # レーンラベル
        svg_parts.append(
            f'<text x="{LABEL_W - 4}" y="{y_main + 4}" fill="#7dd3fc" font-size="9"'
            f' text-anchor="end">main</text>'
        )
        svg_parts.append(
            f'<text x="{LABEL_W - 4}" y="{y_shadow + 4}" fill="#c084fc" font-size="9"'
            f' text-anchor="end">shadow</text>'
        )

        # レーン区切り水平線
        svg_parts.append(
            f'<line x1="{LABEL_W}" y1="{y_center}" x2="{LABEL_W + TIMELINE_W}" y2="{y_center}"'
            f' stroke="#1e293b" stroke-width="1"/>'
        )

        # ---- シャドウレーン: スコア曲線 ----
        v2_sorted = sorted(data["v2_events"], key=lambda r: r["ts_ms"])
        score_points: list[tuple[float, float]] = []
        for ev in v2_sorted:
            sc = ev.get("speech_decision_score")
            if sc is not None:
                x = ts_to_x(ev["ts_ms"])
                # スコア 0→1 を shadow レーン上に投影（高いほど上）
                sy = y_shadow + 15 - sc * 28
                score_points.append((x, sy))

        if len(score_points) >= 2:
            pts_str = " ".join(f"{x:.1f},{y:.1f}" for x, y in score_points)
            svg_parts.append(
                f'<polyline points="{pts_str}" fill="none" stroke="#7c3aed"'
                f' stroke-width="1.5" stroke-opacity="0.7" stroke-dasharray="3,2"/>'
            )

        # ---- シャドウレーン: would_start_inference=True マーカー ----
        shadow_signals: list[dict] = []
        for ev in v2_sorted:
            if ev.get("would_start_inference") is True:
                x = ts_to_x(ev["ts_ms"])
                rel_ms = ev["ts_ms"] - global_start
                sc = ev.get("speech_decision_score", 0.0)
                sat = ev.get("semantic_saturation", 0.0)
                stable = ev.get("stable_text") or ""
                svg_parts.append(
                    f'<polygon points="{x:.1f},{y_shadow - 14} {x - 7:.1f},{y_shadow + 6}'
                    f' {x + 7:.1f},{y_shadow + 6}"'
                    f' fill="#a855f7" stroke="#e879f9" stroke-width="1.5"'
                    f' class="marker shadow-signal"'
                    f' data-turn="{row_idx}" data-ts="{rel_ms}" data-score="{sc:.3f}"'
                    f' data-sat="{sat:.2f}" data-stable="{_escape(stable)}">'
                    f'<title>Shadow signal: +{rel_ms}ms\\nscore={sc:.3f} sat={sat:.2f}\\n{_escape(stable)}</title>'
                    f'</polygon>'
                )
                shadow_signals.append({"ts_ms": ev["ts_ms"], "score": sc, "sat": sat, "stable": stable})

        # ---- メインレーン: provisional_inference_start ----
        prov_events = [e for e in data["main_events"] if e.get("event") == "provisional_inference_start"]
        prov_markers: list[dict] = []
        for ev in sorted(prov_events, key=lambda r: r["ts_ms"]):
            x = ts_to_x(ev["ts_ms"])
            rel_ms = ev["ts_ms"] - global_start
            stable = ev.get("text") or ""
            svg_parts.append(
                f'<circle cx="{x:.1f}" cy="{y_main}" r="8"'
                f' fill="#0ea5e9" stroke="#38bdf8" stroke-width="2"'
                f' class="marker prov-inf"'
                f' data-turn="{row_idx}" data-ts="{rel_ms}" data-text="{_escape(stable)}">'
                f'<title>Provisional inference start: +{rel_ms}ms\\n{_escape(stable)}</title>'
                f'</circle>'
            )
            svg_parts.append(
                f'<line x1="{x:.1f}" y1="{y_main + 8}" x2="{x:.1f}" y2="{y_shadow - 16}"'
                f' stroke="#38bdf8" stroke-width="1" stroke-opacity="0.5" stroke-dasharray="3,2"/>'
            )
            prov_markers.append({"ts_ms": ev["ts_ms"], "text": stable})

        # ---- メインレーン: final_transcript_received ----
        for ev in sorted(main_finals, key=lambda r: r["ts_ms"]):
            x = ts_to_x(ev["ts_ms"])
            rel_ms = ev["ts_ms"] - global_start
            decision = ev.get("decision") or "-"
            text = ev.get("text") or ""

            # リードタイム矢印（prov があれば）
            if prov_markers:
                prov_x = ts_to_x(prov_markers[0]["ts_ms"])
                if x > prov_x + 4:
                    svg_parts.append(
                        f'<line x1="{prov_x + 8:.1f}" y1="{y_main}" x2="{x - 12:.1f}" y2="{y_main}"'
                        f' stroke="#22d3ee" stroke-width="1.5" marker-end="url(#arrow)"/>'
                    )
                    mid_x = (prov_x + x) / 2
                    lead_ms = ev["ts_ms"] - prov_markers[0]["ts_ms"]
                    svg_parts.append(
                        f'<text x="{mid_x:.1f}" y="{y_main - 10}" fill="#22d3ee" font-size="9"'
                        f' text-anchor="middle">{lead_ms}ms lead</text>'
                    )

            color = "#22d3ee" if decision not in ("restart_with_new_input", "stop_speaking") else "#94a3b8"
            svg_parts.append(
                f'<rect x="{x - 6:.1f}" y="{y_main - 10}" width="12" height="20"'
                f' rx="2" fill="{color}" stroke="#e2e8f0" stroke-width="1"'
                f' class="marker main-final"'
                f' data-turn="{row_idx}" data-ts="{rel_ms}" data-decision="{_escape(decision)}"'
                f' data-text="{_escape(text[:40])}">'
                f'<title>Main final: +{rel_ms}ms\\ndecision={decision}\\n{_escape(text)}</title>'
                f'</rect>'
            )

        # ターンデータ（JS 用）
        turn_data_json.append({
            "turn_id": tid,
            "turn_index": row_idx + 1,
            "text": text_label,
            "main_finals": [
                {
                    "ts_ms": e["ts_ms"],
                    "rel_ms": e["ts_ms"] - global_start,
                    "decision": e.get("decision"),
                    "text": e.get("text"),
                }
                for e in main_finals
            ],
            "prov_inferences": prov_markers,
            "shadow_signals": shadow_signals,
            "v2_count": len(data["v2_events"]),
        })

    # 凡例
    legend_y = SVG_H - 28
    legend_x = LABEL_W
    svg_parts.append(f'<text x="{legend_x}" y="{legend_y}" fill="#94a3b8" font-size="11">凡例:</text>')
    legend_x += 44
    # メイン最終決定
    svg_parts.append(f'<rect x="{legend_x}" y="{legend_y - 9}" width="14" height="14" rx="2" fill="#22d3ee"/>')
    svg_parts.append(f'<text x="{legend_x + 18}" y="{legend_y}" fill="#cbd5e1" font-size="11">メイン推論開始 (final)</text>')
    legend_x += 160
    # provisional
    svg_parts.append(f'<circle cx="{legend_x + 7}" cy="{legend_y - 3}" r="7" fill="#0ea5e9" stroke="#38bdf8" stroke-width="2"/>')
    svg_parts.append(f'<text x="{legend_x + 18}" y="{legend_y}" fill="#cbd5e1" font-size="11">早期推論 (provisional)</text>')
    legend_x += 175
    # shadow signal
    svg_parts.append(
        f'<polygon points="{legend_x + 7},{legend_y - 14} {legend_x},{legend_y - 2} {legend_x + 14},{legend_y - 2}"'
        f' fill="#a855f7" stroke="#e879f9" stroke-width="1.5"/>'
    )
    svg_parts.append(f'<text x="{legend_x + 18}" y="{legend_y}" fill="#cbd5e1" font-size="11">Shadow シグナル (▲)</text>')

    svg_str = "\n".join(svg_parts)
    turn_json_str = json.dumps(turn_data_json, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <title>Turn-taking v2 タイムライン — {session_id[:16]}…</title>
  <style>
    :root {{
      --bg: #020817;
      --surface: #0f172a;
      --border: #1e293b;
      --text: #f1f5f9;
      --muted: #64748b;
      --accent-main: #22d3ee;
      --accent-prov: #0ea5e9;
      --accent-shadow: #a855f7;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: var(--bg);
      color: var(--text);
      font-family: 'Inter', 'Noto Sans JP', system-ui, sans-serif;
      min-height: 100vh;
      padding: 2rem;
    }}
    header {{
      margin-bottom: 2rem;
    }}
    h1 {{
      font-size: 1.5rem;
      font-weight: 700;
      background: linear-gradient(135deg, #22d3ee, #a855f7);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
      margin-bottom: 0.4rem;
    }}
    .subtitle {{
      color: var(--muted);
      font-size: 0.85rem;
      font-family: monospace;
    }}
    .stats-bar {{
      display: flex;
      gap: 1.5rem;
      margin-bottom: 2rem;
      flex-wrap: wrap;
    }}
    .stat {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0.6rem 1.2rem;
      font-size: 0.85rem;
    }}
    .stat-value {{
      font-size: 1.4rem;
      font-weight: 700;
      display: block;
      margin-bottom: 2px;
    }}
    .stat-value.cyan {{ color: var(--accent-main); }}
    .stat-value.blue {{ color: var(--accent-prov); }}
    .stat-value.purple {{ color: var(--accent-shadow); }}
    .stat-label {{ color: var(--muted); font-size: 0.75rem; }}
    .timeline-container {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      overflow-x: auto;
      padding: 1rem;
      margin-bottom: 2rem;
    }}
    svg {{
      display: block;
      overflow: visible;
    }}
    .marker {{ cursor: pointer; transition: opacity 0.15s; }}
    .marker:hover {{ opacity: 0.7; }}
    .detail-panel {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1.5rem;
      min-height: 6rem;
    }}
    .detail-panel h2 {{
      font-size: 1rem;
      margin-bottom: 1rem;
      color: var(--muted);
    }}
    #detail-content {{
      font-size: 0.9rem;
      line-height: 1.8;
    }}
    table.turn-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.82rem;
      margin-top: 1.5rem;
    }}
    table.turn-table th {{
      background: #1e293b;
      color: var(--muted);
      padding: 0.5rem 0.75rem;
      text-align: left;
      font-weight: 600;
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    table.turn-table td {{
      padding: 0.5rem 0.75rem;
      border-bottom: 1px solid var(--border);
      vertical-align: top;
    }}
    table.turn-table tr:hover td {{ background: #1e293b44; }}
    .badge {{
      display: inline-block;
      padding: 1px 6px;
      border-radius: 4px;
      font-size: 0.72rem;
      font-weight: 600;
    }}
    .badge-good {{ background: #0e7490; color: #a5f3fc; }}
    .badge-warn {{ background: #7c2d12; color: #fdba74; }}
    .badge-info {{ background: #4c1d95; color: #d8b4fe; }}
    .badge-neutral {{ background: #1e293b; color: #94a3b8; }}
    footer {{
      margin-top: 2rem;
      color: var(--muted);
      font-size: 0.75rem;
      text-align: center;
    }}
  </style>
</head>
<body>
<header>
  <h1>Turn-taking v2 タイムライン</h1>
  <p class="subtitle">Session: {session_id}</p>
</header>

<div class="stats-bar" id="stats-bar">
  <!-- JS で動的生成 -->
</div>

<div class="timeline-container">
<svg width="{LABEL_W + TIMELINE_W + 60}" height="{SVG_H}"
     xmlns="http://www.w3.org/2000/svg">
  <defs>
    <marker id="arrow" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto">
      <polygon points="0 0, 8 3, 0 6" fill="#22d3ee"/>
    </marker>
  </defs>
  <!-- ヘッダー背景 -->
  <rect x="0" y="0" width="{LABEL_W + TIMELINE_W + 60}" height="{HEADER_H}"
    fill="#0d1526"/>
  <text x="10" y="{HEADER_H - 18}" fill="#94a3b8" font-size="12" font-weight="600">発話</text>
  <text x="{LABEL_W + 4}" y="{HEADER_H - 18}" fill="#94a3b8" font-size="12" font-weight="600">
    相対時間 (セッション開始からの経過)
  </text>

{svg_str}
</svg>
</div>

<div class="detail-panel">
  <h2>📊 ターン詳細（SVG のマーカーをクリックで表示）</h2>
  <div id="detail-content">
    <p style="color:#475569">いずれかのマーカーをクリックするとターン詳細が表示されます。</p>
  </div>
</div>

<div class="timeline-container" style="margin-top:1.5rem">
  <h2 style="color:#94a3b8;font-size:1rem;margin-bottom:1rem">📋 ターン一覧</h2>
  <table class="turn-table" id="turn-table">
    <thead>
      <tr>
        <th>#</th>
        <th>発話テキスト</th>
        <th>Shadow シグナル</th>
        <th>早期推論 (prov)</th>
        <th>メイン最終 (rel_ms)</th>
        <th>リードタイム</th>
        <th>判定</th>
      </tr>
    </thead>
    <tbody id="turn-tbody">
    </tbody>
  </table>
</div>

<footer>generated by server/tools/generate_timeline_html.py · tomoko project</footer>

<script>
const TURNS = {turn_json_str};

// --- Stats ---
const statsBar = document.getElementById('stats-bar');
const totalTurns = TURNS.length;
const goodEarly = TURNS.filter(t => t.prov_inferences.length > 0 && t.main_finals.length > 0).length;
const shadowSignals = TURNS.reduce((a, t) => a + t.shadow_signals.length, 0);
const avgLead = (() => {{
  const leads = TURNS
    .filter(t => t.prov_inferences.length > 0 && t.main_finals.length > 0)
    .map(t => t.main_finals[0].rel_ms - t.prov_inferences[0].ts_ms + {global_start});
  // ts_ms は絶対値なので差分だけ取る
  const leads2 = TURNS
    .filter(t => t.prov_inferences.length > 0 && t.main_finals.length > 0)
    .map(t => t.main_finals[0].rel_ms - (t.prov_inferences[0].ts_ms - {global_start}));
  if (!leads2.length) return null;
  return (leads2.reduce((a,b) => a+b, 0) / leads2.length).toFixed(0);
}})();

statsBar.innerHTML = `
  <div class="stat">
    <span class="stat-value cyan">${{totalTurns}}</span>
    <span class="stat-label">ユーザー発話ターン</span>
  </div>
  <div class="stat">
    <span class="stat-value blue">${{goodEarly}}</span>
    <span class="stat-label">早期推論あり (provisional)</span>
  </div>
  <div class="stat">
    <span class="stat-value purple">${{shadowSignals}}</span>
    <span class="stat-label">Shadow シグナル総数</span>
  </div>
  ${{avgLead !== null ? `<div class="stat">
    <span class="stat-value cyan">${{avgLead}} ms</span>
    <span class="stat-label">平均リードタイム</span>
  </div>` : ''}}
`;

// --- Table ---
const tbody = document.getElementById('turn-tbody');
TURNS.forEach((t, i) => {{
  const shadowCount = t.shadow_signals.length;
  const hasProv = t.prov_inferences.length > 0;
  const mainFinal = t.main_finals[0];
  const finalRelMs = mainFinal ? mainFinal.rel_ms : '-';
  const leadMs = (hasProv && mainFinal)
    ? (mainFinal.ts_ms - t.prov_inferences[0].ts_ms)
    : null;

  let badge = '<span class="badge badge-neutral">safe_wait</span>';
  if (hasProv && leadMs !== null && leadMs > 0) {{
    badge = `<span class="badge badge-good">+${{leadMs}}ms lead</span>`;
  }} else if (shadowCount > 0 && !hasProv) {{
    badge = '<span class="badge badge-warn">missed?</span>';
  }}

  const shadowBadge = shadowCount > 0
    ? `<span class="badge badge-info">${{shadowCount}} 件</span>`
    : '<span style="color:#334155">—</span>';

  const provBadge = hasProv
    ? `<span class="badge badge-good">${{t.prov_inferences.length}} 件</span>`
    : '<span style="color:#334155">—</span>';

  tbody.innerHTML += `
    <tr data-turn="${{i}}" style="cursor:pointer" onclick="showDetail(${{i}})">
      <td style="color:#64748b">${{t.turn_index}}</td>
      <td style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
          title="${{t.text}}">${{t.text || '(テキスト未取得)'}}</td>
      <td>${{shadowBadge}}</td>
      <td>${{provBadge}}</td>
      <td style="font-family:monospace">${{finalRelMs !== '-' ? '+' + finalRelMs + 'ms' : '-'}}</td>
      <td style="font-family:monospace">${{leadMs !== null ? '+' + leadMs + 'ms' : '—'}}</td>
      <td>${{badge}}</td>
    </tr>
  `;
}});

// --- マーカークリック / 詳細表示 ---
document.querySelectorAll('.marker').forEach(el => {{
  el.addEventListener('click', (ev) => {{
    const turnIdx = parseInt(el.dataset.turn);
    showDetail(turnIdx);
    ev.stopPropagation();
  }});
}});

function showDetail(idx) {{
  const t = TURNS[idx];
  if (!t) return;
  const panel = document.getElementById('detail-content');
  const mainFinal = t.main_finals[0];
  const leadMs = (t.prov_inferences.length > 0 && mainFinal)
    ? (mainFinal.ts_ms - t.prov_inferences[0].ts_ms)
    : null;

  panel.innerHTML = `
    <div style="margin-bottom:1rem">
      <strong style="color:#7dd3fc">Turn ${{t.turn_index}}</strong>
      <span style="color:#475569;margin-left:0.5rem;font-family:monospace;font-size:0.8rem">${{t.turn_id}}</span>
    </div>
    <div style="background:#0d1526;border-radius:8px;padding:0.75rem 1rem;margin-bottom:0.75rem;font-family:monospace;font-size:0.85rem">
      「${{t.text || '(テキスト未取得)'}}」
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:1rem">
      <div>
        <div style="color:#94a3b8;font-size:0.75rem;margin-bottom:0.3rem">Shadow シグナル (${{t.shadow_signals.length}}件)</div>
        ${{t.shadow_signals.length > 0
          ? t.shadow_signals.map(s => `
            <div style="font-family:monospace;font-size:0.78rem;color:#c084fc">
              rel=+${{s.ts_ms - {global_start}}}ms score=${{s.score.toFixed(3)}} sat=${{s.sat.toFixed(2)}}
              ${{s.stable ? '<br>stable: ' + s.stable : ''}}
            </div>`).join('')
          : '<span style="color:#334155">なし</span>'
        }}
      </div>
      <div>
        <div style="color:#94a3b8;font-size:0.75rem;margin-bottom:0.3rem">早期推論 (${{t.prov_inferences.length}}件)</div>
        ${{t.prov_inferences.length > 0
          ? t.prov_inferences.map(p => `
            <div style="font-family:monospace;font-size:0.78rem;color:#7dd3fc">
              rel=+${{p.ts_ms - {global_start}}}ms
              ${{p.text ? '<br>text: ' + p.text : ''}}
            </div>`).join('')
          : '<span style="color:#334155">なし</span>'
        }}
      </div>
      <div>
        <div style="color:#94a3b8;font-size:0.75rem;margin-bottom:0.3rem">メイン最終決定</div>
        ${{mainFinal
          ? `<div style="font-family:monospace;font-size:0.78rem;color:#22d3ee">
              rel=+${{mainFinal.rel_ms}}ms<br>
              decision: ${{mainFinal.decision}}<br>
              ${{leadMs !== null ? '<strong>リードタイム: ' + leadMs + 'ms</strong>' : ''}}
             </div>`
          : '<span style="color:#334155">なし</span>'
        }}
      </div>
    </div>
  `;

  // テーブル行をハイライト
  document.querySelectorAll('#turn-tbody tr').forEach(tr => tr.style.background = '');
  const row = document.querySelector(`#turn-tbody tr[data-turn="${{idx}}"]`);
  if (row) row.style.background = '#1e293b';
}}
</script>
</body>
</html>
"""
    return html


def _empty_html(session_id: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"/><title>Turn-taking v2 タイムライン</title>
<style>body{{background:#020817;color:#f1f5f9;font-family:system-ui;padding:2rem;}}
h1{{color:#22d3ee;}}p{{color:#64748b;}}</style></head>
<body>
<h1>Turn-taking v2 タイムライン</h1>
<p>Session: {session_id}</p>
<p>このセッションに突合可能なターンデータが見つかりませんでした。</p>
<p>ヒント: main ログの conversation_session_id が null の場合、
ambient モード中（会話セッション未確立）の発話しか記録されていません。</p>
</body></html>
"""
