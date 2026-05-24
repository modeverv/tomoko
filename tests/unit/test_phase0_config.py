from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement

from server.shared.config import NodeConfig

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_central_realtime_config_uses_lfm_mlx_for_main_conversation() -> None:
    config = NodeConfig.load(ROOT / "config" / "central_realtime.toml")

    assert config.node.role == "central_realtime"
    assert config.inference.conversation_backend == "local_lfm25_12b_jp_mlx"
    assert config.inference.conversation_fallback == "local_gemma4_e2b_mlx"
    assert config.inference.tts_backend == "kokoro_mlx"
    assert config.inference.embedding_backend == "local_multilingual_e5_small"
    assert config.inference.speech_normalizer_enabled is False

    backend = config.backends["local_lfm25_12b_jp_mlx"]
    assert backend.type == "mlx_lm"
    assert backend.model == "lmstudio-community/LFM2.5-1.2B-Instruct-MLX-4bit"
    assert backend.privacy_allowed is True

    lm_studio_backend = config.backends["lmstudio_gemma4_e2b"]
    assert lm_studio_backend.type == "lm_studio"
    assert lm_studio_backend.url == "http://192.168.11.66:1234"
    assert lm_studio_backend.model == "gemma-4-e2b-it-mlx"
    assert lm_studio_backend.privacy_allowed is True

    fallback_backend = config.backends["local_gemma4_e2b_mlx"]
    assert fallback_backend.type == "gemma_mlx"
    assert fallback_backend.model == "mlx-community/gemma-4-e2b-it-4bit"
    assert fallback_backend.privacy_allowed is True

    embedding_backend = config.backends["local_multilingual_e5_small"]
    assert embedding_backend.type == "multilingual_e5_small"
    assert embedding_backend.model == "intfloat/multilingual-e5-small"
    assert embedding_backend.privacy_allowed is True

    tts_backend = config.backends["kokoro_mlx"]
    assert tts_backend.type == "kokoro_mlx"
    assert tts_backend.model == "mlx-community/Kokoro-82M-bf16"
    assert tts_backend.voice == "jf_alpha"

    irodori_backend = config.backends["irodori_mlx"]
    assert irodori_backend.type == "irodori_mlx"
    assert irodori_backend.model == "mlx-community/Irodori-TTS-500M-v3-8bit"
    assert irodori_backend.voice == "none"

    irodori_stream_backend = config.backends["irodori_mlx_stream"]
    assert irodori_stream_backend.type == "irodori_mlx_stream"
    assert irodori_stream_backend.model == "mlx-community/Irodori-TTS-500M-v3-8bit"
    assert irodori_stream_backend.voice == "none"

    qwen_small_backend = config.backends["qwen3_tts_mlx_small"]
    assert qwen_small_backend.type == "qwen3_mlx"
    assert qwen_small_backend.model == "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"
    assert qwen_small_backend.voice == "none"

    qwen_large_backend = config.backends["qwen3_tts_mlx_large"]
    assert qwen_large_backend.type == "qwen3_mlx"
    assert qwen_large_backend.model == "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16"
    assert qwen_large_backend.voice == "none"


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
