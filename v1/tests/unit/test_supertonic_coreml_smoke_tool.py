from __future__ import annotations

from pathlib import Path

import pytest

from _tools.bench_supertonic_coreml_tts import (
    SupertonicRun,
    ensure_voice_style,
    summarize_runs,
)


@pytest.mark.unit
def test_summarize_runs_returns_avg_min_max() -> None:
    summary = summarize_runs(
        [
            SupertonicRun(1, 100.0, 1000.0, 10.0, 0.1, 0.2, "a.wav"),
            SupertonicRun(2, 120.0, 1000.0, 8.3, 0.1, 0.2, "b.wav"),
        ]
    )

    assert summary == {"avg_ms": 110.0, "min_ms": 100.0, "max_ms": 120.0}


@pytest.mark.unit
def test_summarize_runs_rejects_empty() -> None:
    with pytest.raises(ValueError, match="at least one run"):
        summarize_runs([])


@pytest.mark.unit
def test_ensure_voice_style_keeps_existing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "F1.json"
    path.write_text("{}")

    def fail_download(_repo: str, _file: str) -> str:
        raise AssertionError("download should not be called")

    monkeypatch.setattr(
        "_tools.bench_supertonic_coreml_tts.hf_hub_download",
        fail_download,
    )

    ensure_voice_style("F1", path)

    assert path.read_text() == "{}"
