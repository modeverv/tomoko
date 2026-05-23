from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
import pytest

from server.shared.config import NodeConfig

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_central_realtime_config_uses_ollama_for_m1() -> None:
    config = NodeConfig.load(ROOT / "config" / "central_realtime.toml")

    assert config.node.role == "central_realtime"
    assert config.inference.conversation_backend == "local_qwen7b"
    assert config.inference.tts_backend == "say"

    backend = config.backends["local_qwen7b"]
    assert backend.type == "ollama"
    assert backend.model == "qwen2.5:7b"
    assert backend.privacy_allowed is True


@pytest.mark.unit
def test_phase0_pytest_markers_are_registered() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    markers = pyproject["tool"]["pytest"]["ini_options"]["markers"]

    assert any(marker.startswith("unit:") for marker in markers)
    assert any(marker.startswith("integration:") for marker in markers)
    assert any(marker.startswith("perf:") for marker in markers)


@pytest.mark.unit
def test_phase0_project_dependencies_are_valid_pep508() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())

    for dependency in pyproject["project"]["dependencies"]:
        Requirement(dependency)


@pytest.mark.unit
def test_postgres_init_enables_required_extensions() -> None:
    sql = (ROOT / "docker" / "postgres" / "init" / "001_extensions.sql").read_text()

    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    assert "CREATE EXTENSION IF NOT EXISTS pgroonga" in sql
