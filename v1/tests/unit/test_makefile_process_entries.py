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
        "THINKER2_LOG_FILE ?= logs/thinker2.log",
        "JOURNALIST_LOG_FILE ?= logs/journalist.log",
        "MONITOR_HOST ?= 127.0.0.1",
        "MONITOR_PORT ?= 8770",
        "BACKEND_TRACE_LOG_FILE ?= logs/backend-trace.jsonl",
        "SYSTEM_METRICS_LOG_FILE ?= logs/system-metrics.jsonl",
        "WORLD_OBSERVATION_LOG_FILE ?= logs/world-observations.log",
        "WORLD_OBSERVATION_MCP_TIMEOUT_SEC ?= 600",
        "WORLD_OBSERVATION_PROVIDER_TIMEOUT_SEC ?= 600",
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
        "thinker2",
        "thinker2-once",
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

    background_once_line = next(
        line for line in MAKEFILE_TEXT.splitlines() if line.startswith("background-once:")
    )
    for target in [
        "information-collect-world",
        "information-ingest-once",
        "information-interpret-once",
        "thinker-once",
    ]:
        assert target in background_once_line
    assert background_once_line.index("information-collect-world") < background_once_line.index(
        "information-ingest-once"
    )
    assert background_once_line.index("information-ingest-once") < background_once_line.index(
        "information-interpret-once"
    )
    assert background_once_line.index("information-interpret-once") < background_once_line.index(
        "thinker-once"
    )

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
        "information-collect-world",
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
    assert "--system-metrics $(SYSTEM_METRICS_LOG_FILE)" in monitor_body
    assert "--config $(CENTRAL_CONFIG)" in monitor_body


@pytest.mark.unit
def test_makefile_exposes_system_metrics_monitor() -> None:
    body = _target_body("system-monitor")

    assert "_tools/system_metrics.py" in body
    assert "--provider $(SYSTEM_METRICS_PROVIDER)" in body
    assert "--command $(SYSTEM_METRICS_COMMAND)" in body
    assert "--output $(SYSTEM_METRICS_LOG_FILE)" in body
    assert "--interval-sec $(SYSTEM_METRICS_INTERVAL_SEC)" in body


@pytest.mark.unit
def test_makefile_exposes_maai_tap_smoke_tool() -> None:
    smoke_body = _target_body("smoke-maai-tap")
    real_body = _target_body("smoke-maai-real")
    dialogue_body = _target_body("smoke-maai-dialogue")
    material_body = _target_body("smoke-maai-material")

    assert "_tools/smoke_maai_tap_session.py" in smoke_body
    assert "_tools/smoke_maai_tap_session.py --use-maai" in real_body
    assert "_tools/smoke_maai_dialogue.py" in dialogue_body
    assert "MAAI_MATERIAL_WAV ?= _tools/materials/maai.wav" in MAKEFILE_TEXT
    assert "MAAI_MATERIAL_START_SEC ?= 0" in MAKEFILE_TEXT
    assert "MAAI_MATERIAL_DURATION_SEC ?= 30" in MAKEFILE_TEXT
    assert "MAAI_MATERIAL_SWAP_CHANNELS ?=" in MAKEFILE_TEXT
    assert "_tools/smoke_maai_material.py --input $(MAAI_MATERIAL_WAV)" in material_body
    assert "--start-sec $(MAAI_MATERIAL_START_SEC)" in material_body
    assert "--duration-sec $(MAAI_MATERIAL_DURATION_SEC)" in material_body
    assert "$(MAAI_MATERIAL_SWAP_CHANNELS)" in material_body


@pytest.mark.unit
def test_makefile_exposes_research_mcp_smoke_tool() -> None:
    body = _target_body("smoke-research-mcp")

    assert "_tools/smoke_research_mcp_flow.py" in body


@pytest.mark.unit
def test_makefile_exposes_research_session_smoke_tool() -> None:
    body = _target_body("smoke-research-session")

    assert "_tools/smoke_research_tomoro_session_flow.py" in body


@pytest.mark.unit
def test_makefile_exposes_world_observation_operator_collection() -> None:
    body = _target_body("information-collect-world")

    assert "_tools/collect_world_observation.py" in body
    assert "--date $(WORLD_OBSERVATION_DATE)" in body
    assert "--output-dir $(WORLD_OBSERVATION_WORK)" in body
    assert "TOMOKO_WORLD_OBSERVATION_MCP_TIMEOUT_SEC=$(WORLD_OBSERVATION_MCP_TIMEOUT_SEC)" in body
    assert (
        "TOMOKO_WORLD_OBSERVATION_PROVIDER_TIMEOUT_SEC=$(WORLD_OBSERVATION_PROVIDER_TIMEOUT_SEC)"
        in body
    )


@pytest.mark.unit
def test_makefile_exposes_thinker2_runtime_entries() -> None:
    body = _target_body("thinker2")
    once_body = _target_body("thinker2-once")
    capture_once_body = _target_body("thinker2-capture-once")

    assert "background-process/run_thinker2.py" in body
    assert "background-process/run_thinker2.py" in once_body
    assert "background-process/run_thinker2.py" in capture_once_body
    assert "TOMOKO_LOG_FILE=$(THINKER2_LOG_FILE)" in body
    assert "--watch" in body
    assert "--once" in once_body
    assert "--inspection-output $(THINKER2_INSPECTION_HTML)" in once_body
    assert "--capture-perception" in capture_once_body
    assert "--infer-perception" in capture_once_body
    assert "--vlm-model $(THINKER2_VLM_MODEL)" in capture_once_body
