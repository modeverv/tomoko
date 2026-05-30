from __future__ import annotations

from pathlib import Path

import pytest

from _tools.monitor_snapshot import (
    build_monitor_snapshot,
    parse_backend_trace_lines,
    parse_context_event,
    parse_server_debug_lines,
)


@pytest.mark.unit
def test_parse_context_event_extracts_depth_and_counts() -> None:
    event = parse_context_event(
        "2026-05-30 12:00:00.001 INFO:server.gateway.context:"
        "ContextSnapshotBuilder depth=deep elapsed_ms=93.4 budget_ms=100 "
        "timed_out=False recent_turns=12 session_summaries=1 memory_hits=2 "
        "lexicon_terms=4"
    )

    assert event is not None
    assert event.depth == "deep"
    assert event.elapsed_ms == 93.4
    assert event.timed_out is False
    assert event.source_counts == {
        "recent_turns": 12,
        "session_summaries": 1,
        "memory_hits": 2,
        "lexicon_terms": 4,
    }


@pytest.mark.unit
def test_parse_server_debug_lines_finds_monitor_events() -> None:
    parsed = parse_server_debug_lines(
        [
            "2026-05-30 12:00:00.001 INFO:server.gateway.context:"
            "ContextSnapshotBuilder depth=deep elapsed_ms=93.4 budget_ms=100 "
            "timed_out=False recent_turns=12 session_summaries=1 memory_hits=2 "
            "lexicon_terms=4",
            "2026-05-30 12:00:00.100 INFO:server.edge.main:transcript_final text='ともこ'",
            "2026-05-30 12:00:00.200 INFO:server.gateway.thinking.fast:"
            "ThinkFastMode llm_prompt backend=lmstudio payload={}",
            "2026-05-30 12:00:00.300 INFO:server.session:initiative_skipped "
            "reason=policy_wait",
            "2026-05-30 12:00:00.400 INFO:server.session:"
            "TomoroSession turn_taking_decision decision=continue_current_reply",
        ]
    )

    assert parsed.latest_context is not None
    assert parsed.latest_context.depth == "deep"
    assert [event.kind for event in parsed.timeline] == [
        "context",
        "transcript",
        "conversation_prompt",
        "initiative",
        "turn_taking",
    ]


@pytest.mark.unit
def test_parse_backend_trace_lines_keeps_recent_json_events() -> None:
    calls = parse_backend_trace_lines(
        [
            '{"trace":"tomoko_backend_call","event":"start","role":"conversation",'
            '"kind":"chat","backend":"lmstudio","request_id":"a"}',
            '{"trace":"other","event":"ignored"}',
            '{"trace":"tomoko_backend_call","event":"first_chunk","role":"conversation",'
            '"kind":"chat","backend":"lmstudio","first_ms":420.0}',
        ],
        limit=5,
    )

    assert [call.event for call in calls] == ["start", "first_chunk"]
    assert calls[1].role == "conversation"
    assert calls[1].backend == "lmstudio"


@pytest.mark.unit
def test_build_monitor_snapshot_reads_logs_without_db(tmp_path: Path) -> None:
    server_log = tmp_path / "server-debug.log"
    backend_trace = tmp_path / "backend-trace.jsonl"
    server_log.write_text(
        "2026-05-30 12:00:00.001 INFO:server.gateway.context:"
        "ContextSnapshotBuilder depth=deep elapsed_ms=93.4 budget_ms=100 "
        "timed_out=False recent_turns=12 session_summaries=1 memory_hits=2 "
        "lexicon_terms=4\n"
    )
    backend_trace.write_text(
        '{"trace":"tomoko_backend_call","event":"done","role":"conversation",'
        '"kind":"chat","backend":"lmstudio","total_ms":800.0}\n'
    )

    snapshot = build_monitor_snapshot(
        server_log_path=server_log,
        backend_trace_path=backend_trace,
        config_path=None,
        log_tail_lines=100,
    )

    assert snapshot["context"]["latest"]["depth"] == "deep"
    assert snapshot["backend_trace"]["recent_calls"][0]["total_ms"] == 800.0
    assert snapshot["database"]["available"] is False
