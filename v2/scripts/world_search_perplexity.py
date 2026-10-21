#!/usr/bin/env python3
"""tomoko-research-operator(Perplexity)を world_search backend 形式に変換するアダプタ。

TOMOKO_V2_WORLD_SEARCH_CMD から `... <query>` で呼ばれ、stdout に
{"items": [{"source_key", "title", "text", "confidence"}]} を返す。
operator が failed/timeout/needs_human を返した場合は items=[] で正常終了する
(sense request は done になり、会話側は謝りフォールバックを話す)。
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from typing import Any

DEFAULT_OPERATOR_DIR = "/Users/seijiro/Sync/sync_work/by-llms/tomoko-research-operator"
DEFAULT_RUN_PREFIX = "mise x python@3.14 uv@0.11.16 -- uv run"


def items_from_research_result(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("status") != "completed":
        return []
    trace = str(payload.get("provider_trace_id") or "perplexity")
    confidence = float(payload.get("confidence") or 0.7)
    items: list[dict[str, Any]] = []
    short_answer = str(payload.get("short_answer") or "").strip()
    source_note = ""
    citations = payload.get("citations") or []
    names = [
        str(citation.get("source") or citation.get("title") or "").strip()
        for citation in citations[:2]
    ]
    names = [name for name in names if name]
    if names:
        source_note = f"(出典: {'、'.join(names)})"
    if short_answer:
        items.append(
            {
                "source_key": f"{trace}-answer",
                "title": str(payload.get("query", "")),
                "text": f"{short_answer}{source_note}",
                "confidence": confidence,
            }
        )
    for index, bullet in enumerate(list(payload.get("bullets") or [])[:3]):
        text = str(bullet).strip()
        if text:
            items.append(
                {
                    "source_key": f"{trace}-bullet-{index}",
                    "title": str(payload.get("query", "")),
                    "text": text,
                    "confidence": confidence,
                }
            )
    return items


def _run_operator_once(query: str) -> dict[str, Any] | None:
    operator_dir = os.environ.get("TOMOKO_RESEARCH_OPERATOR_DIR", DEFAULT_OPERATOR_DIR)
    run_prefix = shlex.split(
        os.environ.get("TOMOKO_RESEARCH_RUN_PREFIX", DEFAULT_RUN_PREFIX)
    )
    timeout_sec = float(os.environ.get("TOMOKO_RESEARCH_TIMEOUT_SEC", "90"))
    argv = [
        *run_prefix,
        "tomoko-research",
        "search",
        query,
        "--mode",
        os.environ.get("TOMOKO_RESEARCH_MODE", "quick"),
        "--timeout-sec",
        str(timeout_sec),
    ]
    try:
        completed = subprocess.run(
            argv,
            cwd=operator_dir,
            capture_output=True,
            text=True,
            timeout=timeout_sec + 30.0,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"research operator invocation failed: {exc}", file=sys.stderr)
        return None
    if completed.returncode != 0:
        print(
            f"research operator rc={completed.returncode}: "
            f"{completed.stderr.strip()[:300]}",
            file=sys.stderr,
        )
        return None
    # 出力の最終行が ResearchResult JSON(前段にログが混ざる可能性に備える)
    for line in reversed(completed.stdout.strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    print("research operator returned no JSON", file=sys.stderr)
    return None


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print(json.dumps({"items": []}, ensure_ascii=False))
        return 0
    query = sys.argv[1].strip()
    attempts = 1 + int(os.environ.get("TOMOKO_RESEARCH_RETRIES", "1"))
    payload: dict[str, Any] | None = None
    for attempt in range(attempts):
        payload = _run_operator_once(query)
        if payload is not None and payload.get("status") == "completed":
            break
        reason = (payload or {}).get("error_reason") or "invocation failed"
        print(
            f"research attempt {attempt + 1}/{attempts} not completed: {reason}",
            file=sys.stderr,
        )
    if payload is None:
        return 1
    print(json.dumps({"items": items_from_research_result(payload)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
