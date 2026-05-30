from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement

from server.shared.config import NodeConfig

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_central_realtime_config_uses_lmstudio_gemma4_26b_for_main_conversation() -> None:
    config = NodeConfig.load(ROOT / "config" / "central_realtime.toml")

    assert config.node.role == "central_realtime"
    assert config.inference.conversation_backend == "lmstudio_gemma4_26b_a4b"
    assert config.inference.conversation_fallback == "local_gemma4_e2b_mlx"
    assert config.inference.memory_extraction_backend == "lmstudio_gemma4_31b"
    assert config.inference.memory_extraction_fallback == "local_gemma4_e2b_mlx"
    assert config.inference.persona_update_backend == "lmstudio_gemma4_31b"
    assert config.inference.persona_update_fallback == "local_gemma4_e2b_mlx"
    assert config.audio.vad_silence_ms == 800
    assert config.inference.stt_backend == "local_apple_speech_ja"
    assert config.inference.tts_backend == "voicevox_tsumugi"
    assert config.inference.embedding_backend == "local_bge_m3"
    assert config.inference.speech_normalizer_enabled is False

    backend = config.backends["lmstudio_gemma4_26b_a4b"]
    assert backend.type == "lm_studio"
    assert backend.url == "http://192.168.11.66:1234"
    assert backend.model == "gemma-4-26b-a4b-it-mlx"
    assert backend.max_latency_ms == 5000
    assert backend.privacy_allowed is True

    e4b_backend = config.backends["lmstudio_gemma4_e4b"]
    assert e4b_backend.type == "lm_studio"
    assert e4b_backend.url == "http://192.168.11.66:1234"
    assert e4b_backend.model == "gemma-4-e4b-it-mlx"
    assert e4b_backend.privacy_allowed is True

    memory_backend = config.backends["lmstudio_gemma4_31b"]
    assert memory_backend.type == "lm_studio"
    assert memory_backend.url == "http://192.168.11.66:1234"
    assert memory_backend.model == "gemma-4-31b-it-mlx"
    assert memory_backend.max_latency_ms == 60000
    assert memory_backend.privacy_allowed is True

    lm_studio_backend = config.backends["lmstudio_gemma4_e2b"]
    assert lm_studio_backend.type == "lm_studio"
    assert lm_studio_backend.url == "http://192.168.11.66:1234"
    assert lm_studio_backend.model == "gemma-4-e2b-it-mlx"
    assert lm_studio_backend.privacy_allowed is True

    fallback_backend = config.backends["local_lfm25_12b_jp_mlx"]
    assert fallback_backend.type == "mlx_lm"
    assert fallback_backend.model == "lmstudio-community/LFM2.5-1.2B-Instruct-MLX-4bit"
    assert fallback_backend.privacy_allowed is True

    stt_backend = config.backends["local_whisperkit_serve_large_turbo_632m_cpu_ne"]
    assert stt_backend.type == "whisperkit_serve"
    assert stt_backend.url == "http://127.0.0.1:50062"
    assert stt_backend.model == "large-v3-v20240930_turbo_632MB"
    assert stt_backend.compute_units == "cpuAndNeuralEngine"
    assert stt_backend.streaming is True

    mlx_stt_backend = config.backends["local_whisper_mlx_large_turbo_q4"]
    assert mlx_stt_backend.type == "mlx_whisper"
    assert mlx_stt_backend.model == "mlx-community/whisper-large-v3-turbo-q4"

    apple_speech_backend = config.backends[config.inference.stt_backend]
    assert apple_speech_backend.type == "apple_speech"
    assert apple_speech_backend.language == "ja-JP"
    assert apple_speech_backend.on_device is True
    assert apple_speech_backend.streaming is False

    embedding_backend = config.backends["local_bge_m3"]
    assert embedding_backend.type == "bge_m3"
    assert embedding_backend.model == "BAAI/bge-m3"
    assert embedding_backend.dimensions == 1024
    assert embedding_backend.privacy_allowed is True

    tts_backend = config.backends["kokoro_mlx"]
    assert tts_backend.type == "kokoro_mlx"
    assert tts_backend.model == "mlx-community/Kokoro-82M-bf16"
    assert tts_backend.voice == "jf_alpha"
    assert tts_backend.sample_rate == 24000

    voicevox_backend = config.backends["voicevox_tsumugi"]
    assert voicevox_backend.type == "voicevox"
    assert voicevox_backend.url == "http://127.0.0.1:50021"
    assert voicevox_backend.voice == "8"
    assert voicevox_backend.sample_rate == 24000

    voicevox_stream_backend = config.backends["voicevox_tsumugi_stream"]
    assert voicevox_stream_backend.type == "voicevox_stream"
    assert voicevox_stream_backend.url == "http://127.0.0.1:50021"
    assert voicevox_stream_backend.voice == "8"
    assert voicevox_stream_backend.sample_rate == 24000

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
    pytest_options = pyproject["tool"]["pytest"]["ini_options"]
    markers = pytest_options["markers"]

    assert pytest_options["addopts"] == "-m unit"
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
