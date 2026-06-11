"""
Turn-taking v2 分析ラッパー: 最新セッションを自動取得してレポート生成。

使い方:
    # 最新1セッションを分析
    python -m server.tools.analyze_turn_taking_v2_latest

    # 全有効セッションを一覧表示
    python -m server.tools.analyze_turn_taking_v2_latest --list-sessions

    # 上位N件を分析
    python -m server.tools.analyze_turn_taking_v2_latest --top 3

    # カスタムログパス
    python -m server.tools.analyze_turn_taking_v2_latest --main logs/turn-taking-main.jsonl --v2 logs/turn-taking-v2-shadow.jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from server.tools.analyze_turn_taking_v2 import generate_report, load_jsonl


def _load_sessions(
    main_path: Path, v2_path: Path
) -> list[dict[str, Any]]:
    """
    両ログから、main と v2 の両方に記録があるセッションを列挙する。
    main 側 session_id が null のレコードは除外する。
    戻り値は最終登場 ts_ms の降順（新しい順）でソート済み。
    """
    main_recs = load_jsonl(main_path)
    v2_recs = load_jsonl(v2_path)

    # session_id → 最終 ts_ms + v2 件数
    session_info: dict[str, dict[str, Any]] = {}

    for r in main_recs:
        sid = r.get("conversation_session_id")
        if not sid:
            continue
        info = session_info.setdefault(sid, {"last_ts": 0, "main_count": 0, "v2_count": 0})
        info["main_count"] += 1
        info["last_ts"] = max(info["last_ts"], r["ts_ms"])

    for r in v2_recs:
        sid = r.get("conversation_session_id")
        if not sid:
            continue
        info = session_info.setdefault(sid, {"last_ts": 0, "main_count": 0, "v2_count": 0})
        info["v2_count"] += 1
        info["last_ts"] = max(info["last_ts"], r["ts_ms"])

    # main と v2 の両方に1件以上あるものだけを有効セッションとする
    valid = [
        {"session_id": sid, **info}
        for sid, info in session_info.items()
        if info["main_count"] > 0 and info["v2_count"] > 0
    ]

    # 最新順
    valid.sort(key=lambda x: x["last_ts"], reverse=True)
    return valid


def _format_ts(ts_ms: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Turn-taking v2 分析: 最新セッションを自動取得してレポート生成"
    )
    parser.add_argument(
        "--main",
        type=str,
        default="logs/turn-taking-main.jsonl",
        help="メイン意思決定ログのパス",
    )
    parser.add_argument(
        "--v2",
        type=str,
        default="logs/turn-taking-v2-shadow.jsonl",
        help="v2 shadow advisory ログのパス",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default="reports/turn-taking",
        help="レポート出力先ディレクトリ",
    )
    parser.add_argument(
        "--list-sessions",
        action="store_true",
        help="有効なセッション一覧を表示して終了",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=1,
        help="分析する最新セッション数（デフォルト: 1）",
    )
    parser.add_argument(
        "--html",
        action="store_true",
        help="MD レポートに加えて HTML タイムラインも生成する",
    )
    args = parser.parse_args()

    main_path = Path(args.main)
    v2_path = Path(args.v2)

    for p in [main_path, v2_path]:
        if not p.exists():
            print(f"[警告] ログファイルが見つかりません: {p}")

    sessions = _load_sessions(main_path, v2_path)

    if args.list_sessions:
        if not sessions:
            print("突合可能なセッションが見つかりません。")
            print("  ・会話を行って logs/ にログが溜まっているか確認してください。")
            print("  ・main ログの conversation_session_id が null の場合、")
            print("    ambient ではなく engaged モード中の発話ログが必要です。")
            return

        print(f"{'#':<4} {'Session ID':<40} {'Last seen':<24} {'main':>6} {'v2':>6}")
        print("-" * 90)
        for i, s in enumerate(sessions, 1):
            print(
                f"{i:<4} {s['session_id']:<40} {_format_ts(s['last_ts']):<24}"
                f" {s['main_count']:>6} {s['v2_count']:>6}"
            )
        return

    if not sessions:
        print("突合可能なセッションが見つかりません。")
        print("")
        print("原因として多いのは:")
        print("  ・main ログ側の conversation_session_id がすべて null")
        print("    → ambient モード中（会話セッション未確立）の発話しか記録されていない")
        print("    → ともこに話しかけて engaged モードに入ったターンのログが必要です")
        print("")
        print("現在の v2 shadow ログに含まれるセッション:")
        v2_recs = load_jsonl(v2_path)
        seen = set()
        for r in v2_recs:
            sid = r.get("conversation_session_id")
            if sid and sid not in seen:
                seen.add(sid)
                print(f"  {sid}")
        if not seen:
            print("  （なし）")
        return

    out_dir = Path(args.out_dir)
    targets = sessions[: args.top]

    for s in targets:
        sid = s["session_id"]
        main_recs = load_jsonl(main_path)
        v2_recs = load_jsonl(v2_path)
        report = generate_report(sid, main_recs, v2_recs)

        out_path = out_dir / f"{sid}.md"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        print(f"✓ レポート生成: {out_path}  (main={s['main_count']}, v2={s['v2_count']}件)")

        if args.html:
            from server.tools.generate_timeline_html import generate_html_timeline
            html_path = out_dir / f"{sid}.html"
            html_content = generate_html_timeline(sid, main_recs, v2_recs)
            html_path.write_text(html_content, encoding="utf-8")
            print(f"✓ HTMLタイムライン生成: {html_path}")

    if len(sessions) > args.top:
        print(f"\n他に {len(sessions) - args.top} セッションあります。")
        print("  make analyze-v2-list   で一覧表示")
        print(f"  make analyze-v2-latest TOP={args.top + 1}  で件数を増やして分析")


if __name__ == "__main__":
    main()
