from __future__ import annotations

import json
from pathlib import Path

import pytest

from _tools.smoke_research_tomoro_session_flow import (
    run_tomoro_session_research_smoke,
)


@pytest.mark.unit
async def test_tomoro_session_research_smoke_runs_wait_reply_and_followup(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "research-session-smoke.json"

    summary = await run_tomoro_session_research_smoke(output_path=output_path)

    loaded = json.loads(output_path.read_text(encoding="utf-8"))
    assert loaded == summary
    assert loaded["ok"] is True
    assert loaded["detected_query"] == "オバマ大統領について"
    assert loaded["wait_prompt_has_response_directive"] is True
    assert loaded["wait_prompt_forbids_answering"] is True
    assert loaded["wait_reply_text"] == (
        "調べてみるね。少し待って。"
        "調べ終わったよ。結果を教えてって言ってね。"
    )
    assert loaded["answer_requested"] is True
    assert loaded["answer_reply_text"] == (
        "オバマ大統領について について調べたよ。"
        "バラク・オバマはアメリカ合衆国の第44代大統領です。"
    )
    assert loaded["ingested_research_count"] == 1
    assert loaded["reply_done_count"] == 3
