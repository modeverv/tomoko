from __future__ import annotations

from pathlib import Path

import pytest

from _tools.analyze_server_debug_log import (
    classify_event,
    parse_log_lines,
    read_tail_lines,
    write_html_report,
)


@pytest.mark.unit
def test_parse_log_lines_splits_runs_and_classifies_events() -> None:
    parsed = parse_log_lines(
        [
            "INFO:     Started server process [100]",
            "INFO:     Waiting for application startup.",
            (
                "2026-05-30 12:00:00.001 INFO:server.edge.main:"
                "startup warm-up started target=stt backend=local_apple_speech_ja"
            ),
            (
                "2026-05-30 12:00:01.001 INFO:server.session:"
                "TomoroSession turn_taking_decision decision=stop_speaking "
                "reason=wait_keyword"
            ),
            (
                "2026-05-30 12:00:01.200 INFO:server.gateway.thinking.fast:"
                "ThinkFastMode llm_prompt backend=lmstudio_gemma4_26b_a4b "
                'payload={"system_prompt":"base","messages":[]}'
            ),
            "INFO:     Finished server process [100]",
            "INFO:     Started server process [200]",
            "WARNING:  WatchFiles detected changes in 'server/session.py'. Reloading...",
            "2026-05-30 12:01:00.001 INFO:server.edge.main:transcript_final text='ともこ'",
            "2026-05-30 12:01:01.001 INFO:server.session:initiative_skipped reason=policy_wait",
        ],
        source="sample.log",
    )

    assert [run.pid for run in parsed.runs] == ["100", "200"]
    assert parsed.runs[0].line_count == 6
    assert parsed.runs[1].line_count == 4
    assert parsed.category_counts["startup"] == 4
    assert parsed.category_counts["turn_taking"] == 1
    assert parsed.category_counts["conversation_prompt"] == 1
    assert parsed.category_counts["reload"] == 1
    assert parsed.category_counts["transcript"] == 1
    assert parsed.category_counts["initiative"] == 1


@pytest.mark.unit
def test_classify_event_prioritizes_warning_and_error() -> None:
    assert (
        classify_event(
            level="WARNING",
            logger="server.session",
            message="TomoroSession turn_taking_decision",
            raw="WARNING: x",
        )
        == "warning"
    )
    assert (
        classify_event(
            level="ERROR",
            logger="server.edge.main",
            message="transcript failed",
            raw="ERROR: x",
        )
        == "error"
    )


@pytest.mark.unit
def test_write_html_report_contains_static_controls(tmp_path: Path) -> None:
    parsed = parse_log_lines(
        [
            "INFO:     Started server process [100]",
            "2026-05-30 12:00:00.001 INFO:server.edge.main:transcript_final text='ともこ'",
        ]
    )
    output_path = tmp_path / "report.html"

    write_html_report(parsed, output_path)

    html = output_path.read_text()
    assert 'id="densitySlider"' in html
    assert 'id="categoryList"' in html
    assert 'id="matchingRunsOnly"' in html
    assert 'id="timeline"' in html
    assert "initialRunIndex" in html
    assert "runCountHtml" in html
    assert "conversation_prompt" in html
    assert "transcript_final" in html
    assert "Started server process" in html


@pytest.mark.unit
def test_read_tail_lines_limits_input(tmp_path: Path) -> None:
    log_path = tmp_path / "server-debug.log"
    log_path.write_text("a\nb\nc\n")

    assert read_tail_lines(log_path, 2) == ["b", "c"]
    assert read_tail_lines(log_path, 0) == ["a", "b", "c"]
