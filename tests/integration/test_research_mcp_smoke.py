from __future__ import annotations

import pytest

from _tools.smoke_research_mcp_flow import run_research_smoke


@pytest.mark.integration
async def test_research_mcp_smoke_runs_session_command_through_subprocess() -> None:
    summary = await run_research_smoke(
        speech_text="ともこ、今日のOpenAI関連ニュースを短く調べて",
    )

    assert summary["ok"] is True
    assert summary["detected_query"] == "今日のOpenAI関連ニュースを短く"
    assert summary["command_count"] == 1
    assert summary["event_types"] == [
        "research_request_accepted",
        "research_result_ready",
    ]
    assert summary["status"] == "completed"
    assert summary["speakable"] is True
    assert summary["notice_text"] == "調べ終わったよ。聞く？"
    assert summary["citation_count"] == 1
    assert summary["provider_trace_id"] == "fake-trace-1"
