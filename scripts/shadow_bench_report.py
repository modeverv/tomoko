#!/usr/bin/env python3
"""
shadow_bench_report.py — shadow-bench JSONL から HTML タイムラインレポートを生成

3ポリシー（prod / energy / eager）の発火タイミングを発話ごとに可視化する。

使い方:
  python scripts/shadow_bench_report.py                    # 最新ログを自動選択
  python scripts/shadow_bench_report.py --log logs/shadow-bench-YYYYMMDD-HHMMSS.jsonl
  python scripts/shadow_bench_report.py --open             # 生成後ブラウザで開く
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = _ROOT / "logs"
REPORT_DIR = _ROOT / "reports" / "shadow-bench"

POLICIES = ("prod", "energy", "fusion", "eager")
POLICY_COLORS = {"prod": "#f87171", "energy": "#facc15", "fusion": "#fb923c", "eager": "#c084fc"}


def load_turns(path: Path) -> list[dict]:
    turns = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            turns.append(json.loads(line))
    return turns


def generate_html(turns: list[dict], log_path: Path) -> str:
    max_ms = max((t["main_llm_start_ms"] for t in turns), default=3000)
    max_ms = max(max_ms, 100) * 1.05

    complete_turns = [t for t in turns if t.get("effective_complete")]

    def eff_lead(policy: str) -> float:
        # 実効リード: hit なら lead、miss/未発火は 0（完結発話で平均）
        vals = []
        for t in complete_turns:
            l = t.get(f"{policy}_lead_ms")
            vals.append(l if (l is not None and t.get(f"{policy}_hit")) else 0.0)
        return sum(vals) / len(vals) if vals else 0.0

    def miss_count(policy: str) -> int:
        return sum(1 for t in turns
                   if t.get(f"{policy}_first_ms") is not None and not t.get(f"{policy}_hit"))

    eager_premature = sum(1 for t in turns if t.get("eager_premature"))
    fusion_premature = sum(1 for t in turns if t.get("fusion_premature"))

    LANE_H = 64
    HEADER_H = 40
    SVG_W = 960
    SVG_H = HEADER_H + len(turns) * LANE_H + 20
    LABEL_W = 150

    def px(ms: float) -> float:
        return LABEL_W + (ms / max_ms) * (SVG_W - LABEL_W - 20)

    swimlane_html = ""
    rows_html = ""

    for idx, t in enumerate(turns):
        y_base = HEADER_H + idx * LANE_H
        y_mid = y_base + LANE_H // 2
        dur = t["audio_duration_ms"]
        main_ms = t["main_llm_start_ms"]

        # 音声区間 / silence〜final 区間
        swimlane_html += (
            f'<rect x="{px(0):.1f}" y="{y_base + 18}" width="{px(dur) - px(0):.1f}" height="14" '
            f'fill="#334155" rx="3" opacity="0.8"/>\n'
            f'<rect x="{px(dur):.1f}" y="{y_base + 18}" width="{px(main_ms) - px(dur):.1f}" height="14" '
            f'fill="#1e3a5f" rx="3" opacity="0.6"/>\n'
        )

        # speech_decision_score 折れ線
        advisories = t.get("shadow_advisories", [])
        if len(advisories) >= 2:
            pts = []
            for a in advisories:
                x = px(a["arrival_ms"])
                score = a.get("speech_decision_score", 0) or 0
                y = y_base + LANE_H - 6 - score * (LANE_H - 24)
                pts.append(f"{x:.1f},{y:.1f}")
            swimlane_html += (
                f'<polyline points="{" ".join(pts)}" fill="none" stroke="#38bdf8" '
                f'stroke-width="1.2" opacity="0.7"/>\n'
            )

        # ポリシー発火マーカー ▲
        for policy in POLICIES:
            first = t.get(f"{policy}_first_ms")
            if first is None:
                continue
            sx = px(first)
            color = POLICY_COLORS[policy]
            swimlane_html += (
                f'<polygon points="{sx:.1f},{y_mid - 10} {sx - 5:.1f},{y_mid + 3} {sx + 5:.1f},{y_mid + 3}" '
                f'fill="{color}" opacity="0.95"><title>{policy}: {first:.0f}ms</title></polygon>\n'
            )

        # main LLM 開始 ■
        mx = px(main_ms)
        swimlane_html += (
            f'<rect x="{mx - 4:.1f}" y="{y_mid - 8}" width="8" height="16" fill="#e2e8f0">'
            f'<title>main LLM start: {main_ms:.0f}ms</title></rect>\n'
        )

        # fusion のリード矢印
        f_first = t.get("fusion_first_ms")
        if f_first is not None and main_ms - f_first > 1:
            swimlane_html += (
                f'<line x1="{px(f_first):.1f}" y1="{y_mid}" x2="{mx:.1f}" y2="{y_mid}" '
                f'stroke="#4ade80" stroke-width="1.5" stroke-dasharray="4,3" opacity="0.7"/>\n'
                f'<text x="{(px(f_first) + mx) / 2:.1f}" y="{y_mid - 12}" text-anchor="middle" '
                f'font-size="10" fill="#4ade80">+{main_ms - f_first:.0f}ms</text>\n'
            )

        short = t["presented_text"][:10] + ("…" if len(t["presented_text"]) > 10 else "")
        swimlane_html += (
            f'<text x="8" y="{y_mid + 4}" font-size="11" fill="#94a3b8" font-family="monospace">'
            f'#{t["utterance_id"]} [{t["label"][:4]}] {short}</text>\n'
            f'<line x1="0" y1="{y_base + LANE_H}" x2="{SVG_W}" y2="{y_base + LANE_H}" '
            f'stroke="#1e293b" stroke-width="1"/>\n'
        )

        # テーブル行
        def fmt_lead(policy: str) -> str:
            l = t.get(f"{policy}_lead_ms")
            if l is None:
                return '<span class="na">—</span>'
            prem = policy != "prod" and t.get(f"{policy}_premature")
            miss = not t.get(f"{policy}_hit")
            cls = "bad" if (prem or miss) else ("good" if l > 1 else "")
            mark = (" ⚠" if prem else "") + (" ✗" if miss else "")
            return f'<span class="{cls}">+{l:.0f}ms{mark}</span>'

        rows_html += f"""
        <tr>
          <td>{t['utterance_id']}</td>
          <td><span class="tag tag-{t['label']}">{t['label']}</span></td>
          <td class="mono">{t['presented_text'][:28]}{'…' if len(t['presented_text']) > 28 else ''}</td>
          <td>{t['audio_duration_ms']:.0f}ms</td>
          <td>{t['main_llm_start_ms']:.0f}ms</td>
          <td>{fmt_lead('prod')}</td>
          <td>{fmt_lead('energy')}</td>
          <td>{fmt_lead('fusion')}</td>
          <td>{fmt_lead('eager')}</td>
        </tr>"""

    # 時間軸
    tick_step = 1000 if max_ms > 4000 else 500
    for tick in range(0, int(max_ms) + 1, tick_step):
        x = px(tick)
        swimlane_html += (
            f'<line x1="{x:.1f}" y1="{HEADER_H}" x2="{x:.1f}" y2="{SVG_H}" stroke="#1e293b" stroke-width="1"/>\n'
            f'<text x="{x:.1f}" y="{HEADER_H - 5}" text-anchor="middle" font-size="11" fill="#64748b">{tick}ms</text>\n'
        )

    ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Shadow Bench — ポリシー別タイミング比較</title>
<style>
  :root {{
    --bg: #0f172a; --bg2: #1e293b; --bg3: #334155;
    --text: #e2e8f0; --muted: #64748b; --accent: #38bdf8;
    --good: #4ade80; --bad: #f87171; --warn: #facc15;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: 'Inter', 'Hiragino Sans', sans-serif; padding: 24px; }}
  h1 {{ font-size: 1.4rem; margin-bottom: 4px; color: var(--accent); }}
  .meta {{ font-size: 0.8rem; color: var(--muted); margin-bottom: 20px; }}
  .stats {{ display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 22px; }}
  .stat-card {{ background: var(--bg2); border: 1px solid var(--bg3); border-radius: 10px; padding: 12px 18px; min-width: 140px; }}
  .stat-label {{ font-size: 0.72rem; color: var(--muted); margin-bottom: 4px; }}
  .stat-value {{ font-size: 1.4rem; font-weight: 700; color: var(--accent); }}
  .section {{ margin-bottom: 30px; }}
  .section-title {{ font-size: 0.95rem; color: var(--muted); margin-bottom: 10px; border-bottom: 1px solid var(--bg3); padding-bottom: 6px; }}
  svg {{ background: var(--bg2); border-radius: 10px; border: 1px solid var(--bg3); display: block; max-width: 100%; }}
  .legend {{ display: flex; gap: 18px; font-size: 0.78rem; color: var(--muted); margin-top: 8px; flex-wrap: wrap; }}
  .legend span {{ display: flex; align-items: center; gap: 5px; }}
  .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.84rem; background: var(--bg2); border-radius: 10px; overflow: hidden; }}
  th {{ background: var(--bg3); padding: 9px 11px; text-align: left; color: var(--muted); font-weight: 600; }}
  td {{ padding: 7px 11px; border-bottom: 1px solid var(--bg3); }}
  tr:last-child td {{ border-bottom: none; }}
  .mono {{ font-family: monospace; }}
  .good {{ color: var(--good); font-weight: 700; }}
  .bad {{ color: var(--bad); font-weight: 700; }}
  .na {{ color: var(--muted); }}
  .tag {{ font-size: 0.72rem; padding: 2px 7px; border-radius: 9px; font-family: monospace; }}
  .tag-complete {{ background: #14532d; color: #86efac; }}
  .tag-multi {{ background: #422006; color: #fdba74; }}
  .tag-incomplete {{ background: #450a0a; color: #fca5a5; }}
  .tag-filler {{ background: #1e293b; color: #94a3b8; }}
</style>
</head>
<body>
<h1>🎙️ Shadow Bench — ポリシー別タイミング比較</h1>
<p class="meta">ログ: {log_path.name} | 生成: {ts_str} | 発話数: {len(turns)} (完結: {len(complete_turns)})</p>

<div class="stats">
  <div class="stat-card">
    <div class="stat-label">fusion 実効リード（完結発話）</div>
    <div class="stat-value">{eff_lead('fusion'):.0f}<span style="font-size:0.9rem;color:var(--muted)">ms</span></div>
  </div>
  <div class="stat-card">
    <div class="stat-label">energy 実効リード（完結発話）</div>
    <div class="stat-value">{eff_lead('energy'):.0f}<span style="font-size:0.9rem;color:var(--muted)">ms</span></div>
  </div>
  <div class="stat-card">
    <div class="stat-label">eager 実効リード（完結発話）</div>
    <div class="stat-value">{eff_lead('eager'):.0f}<span style="font-size:0.9rem;color:var(--muted)">ms</span></div>
  </div>
  <div class="stat-card">
    <div class="stat-label">miss（投機失敗） fusion / energy / eager</div>
    <div class="stat-value">{miss_count('fusion')}<span style="font-size:0.9rem;color:var(--muted)"> / {miss_count('energy')} / {miss_count('eager')}</span></div>
  </div>
  <div class="stat-card">
    <div class="stat-label">誤発火（発話中） fusion / eager</div>
    <div class="stat-value">{fusion_premature}<span style="font-size:0.9rem;color:var(--muted)"> / {eager_premature}</span></div>
  </div>
</div>

<div class="section">
  <div class="section-title">タイムライン（横軸 = 時間, ▲ = ポリシー発火, ▮ = main LLM 開始）</div>
  <svg width="{SVG_W}" height="{SVG_H}" viewBox="0 0 {SVG_W} {SVG_H}">
    {swimlane_html}
  </svg>
  <div class="legend">
    <span><span class="dot" style="background:#334155"></span> 音声区間</span>
    <span><span class="dot" style="background:#1e3a5f"></span> VAD silence + final確定</span>
    <span><span class="dot" style="background:{POLICY_COLORS['prod']}"></span> ▲ prod 発火</span>
    <span><span class="dot" style="background:{POLICY_COLORS['energy']}"></span> ▲ energy 発火</span>
    <span><span class="dot" style="background:{POLICY_COLORS['eager']}"></span> ▲ eager 発火</span>
    <span><span class="dot" style="background:#e2e8f0"></span> ▮ main LLM 開始</span>
    <span><span class="dot" style="background:#38bdf8"></span> speech_decision_score</span>
    <span><span class="dot" style="background:#4ade80"></span> energy リード</span>
  </div>
</div>

<div class="section">
  <div class="section-title">発話別詳細（リードms、⚠ = 発話中の誤発火、✗ = miss（投機失敗・要再推論））</div>
  <table>
    <thead>
      <tr>
        <th>#</th><th>label</th><th>発話テキスト</th>
        <th>音声時間</th><th>main開始</th>
        <th>prod</th><th>energy</th><th>fusion</th><th>eager</th>
      </tr>
    </thead>
    <tbody>
      {rows_html}
    </tbody>
  </table>
</div>
</body>
</html>"""
    return html


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, default=None)
    parser.add_argument("--open", action="store_true", dest="open_browser")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    log_path = args.log
    if log_path is None:
        candidates = sorted(LOG_DIR.glob("shadow-bench-*.jsonl"), reverse=True)
        if not candidates:
            print("ログが見つかりません。先に shadow_bench.py を実行してください。")
            sys.exit(1)
        log_path = candidates[0]

    print(f"ログ: {log_path}")
    turns = load_turns(log_path)
    html = generate_html(turns, log_path)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = args.out or (REPORT_DIR / (log_path.stem + ".html"))
    out_path.write_text(html, encoding="utf-8")
    print(f"→ レポート: {out_path}")

    if args.open_browser:
        subprocess.run(["open", str(out_path)])


if __name__ == "__main__":
    main()
