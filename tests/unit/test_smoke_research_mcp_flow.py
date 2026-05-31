from __future__ import annotations

import json
from pathlib import Path

import pytest

from _tools.smoke_research_mcp_flow import run_research_smoke


@pytest.mark.unit
async def test_research_mcp_smoke_writes_summary(tmp_path: Path) -> None:
    output_path = tmp_path / "research-smoke.json"

    summary = await run_research_smoke(
        speech_text="ともこ、最近のOpenAIを検索してください",
        output_path=output_path,
    )

    loaded = json.loads(output_path.read_text(encoding="utf-8"))
    assert loaded == summary
    assert loaded["ok"] is True
    assert loaded["detected_query"] == "最近のOpenAIを"
    assert loaded["event_types"][:2] == ["research_request_accepted", "research_result_ready"]
    assert loaded["answer_requested"] is True
    assert loaded["short_answer"] == "最近のOpenAIを についての smoke 応答です。"
    assert loaded["reply_text_deltas"] == ["最近のOpenAIを についての smoke 応答です。"]
    assert loaded["ingested_research_count"] == 1
    assert loaded["deep_research_summaries"] == [
        "最近のOpenAIを の外部調査結果をdeep context用に要約したメモです。"
    ]
