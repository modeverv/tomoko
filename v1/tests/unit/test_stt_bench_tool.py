from __future__ import annotations

from pathlib import Path

import pytest

from _tools.bench_stt_backends import (
    ConcurrentLoadConfig,
    Measurement,
    parse_backend_names,
    summarize_measurements,
    write_json_summary,
)


@pytest.mark.unit
def test_parse_backend_names_skips_empty_items() -> None:
    assert parse_backend_names("local_whisper_mlx_small, , local_whisperkit_serve_small") == [
        "local_whisper_mlx_small",
        "local_whisperkit_serve_small",
    ]


@pytest.mark.unit
def test_summarize_measurements_returns_avg_min_max() -> None:
    summary = summarize_measurements(
        [
            Measurement(elapsed_ms=100.0, text="a"),
            Measurement(elapsed_ms=140.0, text="b"),
            Measurement(elapsed_ms=120.0, text="c"),
        ]
    )

    assert summary.avg_ms == 120.0
    assert summary.min_ms == 100.0
    assert summary.max_ms == 140.0


@pytest.mark.unit
def test_summarize_measurements_rejects_empty_list() -> None:
    with pytest.raises(ValueError, match="at least one measurement"):
        summarize_measurements([])


@pytest.mark.unit
def test_write_json_summary_preserves_japanese_text(tmp_path: Path) -> None:
    output_path = tmp_path / "bench.json"
    write_json_summary(
        output_path,
        config_path=Path("config/central_realtime.toml"),
        audio_path=Path("logs/stt-bench/sample.wav"),
        sample_text="ともこ",
        load_config=ConcurrentLoadConfig(
            tts_backend="kokoro_mlx",
            conversation_backend=None,
            start_delay_ms=20,
            tts_text="うん。",
            conversation_text="短く答えて。",
        ),
        results=[],
    )

    text = output_path.read_text()
    assert '"sample_text": "ともこ"' in text
    assert '"tts_backend": "kokoro_mlx"' in text


@pytest.mark.unit
def test_concurrent_load_label_describes_active_backends() -> None:
    assert (
        ConcurrentLoadConfig(
            tts_backend="kokoro_mlx",
            conversation_backend="local_lfm25_12b_jp_mlx",
            start_delay_ms=20,
            tts_text="うん。",
            conversation_text="短く答えて。",
        ).label
        == "tts:kokoro_mlx+conversation:local_lfm25_12b_jp_mlx"
    )
    assert (
        ConcurrentLoadConfig(
            tts_backend=None,
            conversation_backend=None,
            start_delay_ms=20,
            tts_text="うん。",
            conversation_text="短く答えて。",
        ).label
        == "idle"
    )
