from __future__ import annotations

from pathlib import Path

import pytest

MAKEFILE_TEXT = Path("Makefile").read_text()


def _target_body(target: str) -> str:
    marker = f"{target}:"
    start = MAKEFILE_TEXT.index(marker)
    lines = MAKEFILE_TEXT[start:].splitlines()[1:]
    body: list[str] = []
    for line in lines:
        if line and not line.startswith(("\t", " ")):
            break
        body.append(line)
    return "\n".join(body)


@pytest.mark.unit
def test_makefile_exposes_config_and_log_vars_for_separate_processes() -> None:
    expected_vars = [
        "CENTRAL_CONFIG ?= config/central_realtime.toml",
        "EDGE_KITCHEN_CONFIG ?= config/edge_kitchen.toml",
        "EDGE_KITCHEN_LOG_FILE ?= logs/edge-kitchen.log",
        "SESSION_SUMMARY_LOG_FILE ?= logs/session-summarizer.log",
        "TURN_EMBEDDER_LOG_FILE ?= logs/turn-embedder.log",
        "PERSONA_UPDATE_LOG_FILE ?= logs/persona-updater.log",
        "THINKER_LOG_FILE ?= logs/thinker.log",
        "JOURNALIST_LOG_FILE ?= logs/journalist.log",
        "MONITOR_HOST ?= 127.0.0.1",
        "MONITOR_PORT ?= 8770",
        "BACKEND_TRACE_LOG_FILE ?= logs/backend-trace.jsonl",
        "WORLD_OBSERVATION_LOG_FILE ?= logs/world-observations.log",
        "GCAL_URLS_FILE ?= config/gcal_urls.txt",
    ]

    for expected in expected_vars:
        assert expected in MAKEFILE_TEXT


@pytest.mark.unit
def test_makefile_defaults_persona_updater_once_to_one_session() -> None:
    assert "PERSONA_UPDATE_LIMIT ?= 1" in MAKEFILE_TEXT


@pytest.mark.unit
def test_background_process_targets_pass_the_central_config_explicitly() -> None:
    for target in [
        "session-summarizer",
        "session-summarizer-once",
        "turn-embedder",
        "turn-embedder-once",
        "persona-seed-initial",
        "persona-updater",
        "persona-updater-once",
        "thinker",
        "thinker-once",
        "journalist",
        "journalist-once",
        "information-ingest-once",
        "information-ingest-dry-run",
        "information-interpret-once",
        "information-interpret",
        "gcal",
    ]:
        assert "--config $(CENTRAL_CONFIG)" in _target_body(target)


@pytest.mark.unit
def test_makefile_has_grouped_background_maintenance_entries() -> None:
    for target in ["background-once", "background-dry-run"]:
        assert f"{target}:" in MAKEFILE_TEXT

    dry_run_body = _target_body("background-dry-run")
    for target in [
        "gateway",
        "edge-kitchen",
        "session-summarizer",
        "session-summarizer-once",
        "turn-embedder",
        "turn-embedder-once",
        "persona-seed-initial",
        "persona-updater",
        "persona-updater-once",
        "thinker",
        "thinker-once",
        "journalist",
        "journalist-once",
        "information-ingest-dry-run",
        "information-ingest-once",
        "information-interpret-once",
        "information-interpret",
        "gcal",
    ]:
        assert target in dry_run_body


@pytest.mark.unit
def test_makefile_prepare_uses_current_central_config() -> None:
    prepare_body = _target_body("prepare")

    assert "_tools/prepare_runtime.py" in prepare_body
    assert "--config $(CENTRAL_CONFIG)" in prepare_body


@pytest.mark.unit
def test_makefile_monitor_stays_read_only_and_uses_current_logs() -> None:
    monitor_body = _target_body("monitor")

    assert "_tools/monitor_dashboard.py" in monitor_body
    assert "--server-log $(TOMOKO_DEBUG_LOG_FILE)" in monitor_body
    assert "--backend-trace $(BACKEND_TRACE_LOG_FILE)" in monitor_body
    assert "--config $(CENTRAL_CONFIG)" in monitor_body


@pytest.mark.unit
def test_makefile_exposes_maai_tap_smoke_tool() -> None:
    smoke_body = _target_body("smoke-maai-tap")
    real_body = _target_body("smoke-maai-real")

    assert "_tools/smoke_maai_tap_session.py" in smoke_body
    assert "_tools/smoke_maai_tap_session.py --use-maai" in real_body
