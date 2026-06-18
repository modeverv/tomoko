from __future__ import annotations

import json
from pathlib import Path

import pytest

from _tools.smoke_timer_alarm_session_flow import run_timer_alarm_smoke


@pytest.mark.integration
async def test_timer_alarm_smoke_timer_create_ack_due_and_alarm(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "timer-alarm-smoke.json"

    summary = await run_timer_alarm_smoke(
        transcript_text="ともこ、5分後に教えて",
        output_path=output_path,
    )

    loaded = json.loads(output_path.read_text(encoding="utf-8"))
    assert loaded == summary

    # Phase 1 - timer create
    assert summary["timer_request_accepted"] is True, summary
    assert summary["timer_create_recorded"] is True, summary
    assert summary["timer_entry_id"] is not None, summary
    assert summary["timer_due_at"] is not None, summary
    assert summary["ack_reply_has_content"] is True, summary

    # Phase 2 - due notice (scheduled → due → notified full lifecycle)
    assert summary["due_event_type"] == "timer_alarm_due_speakable", summary
    assert summary["due_notice_command_fired"] is True, summary
    assert summary["due_reply_has_content"] is True, summary
    assert summary["db_row_status"] == "notified", summary

    # Phase 3 - alarm create
    assert summary["alarm_request_accepted"] is True, summary
    assert summary["alarm_create_recorded"] is True, summary
    assert summary["alarm_entry_id"] is not None, summary
    assert summary["alarm_ack_reply_has_content"] is True, summary

    assert summary["ok"] is True, summary
