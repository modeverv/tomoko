from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from _tools.bench_rnnoise_filter import run_rnnoise_filter


@pytest.mark.unit
def test_run_rnnoise_filter_invokes_ffmpeg_arnndn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run = Mock()
    monkeypatch.setattr("subprocess.run", run)

    elapsed_ms = run_rnnoise_filter(
        input_path=tmp_path / "input.wav",
        output_path=tmp_path / "output.wav",
        model_path=tmp_path / "std.rnnn",
    )

    command = run.call_args.args[0]
    assert command[0] == "ffmpeg"
    assert "arnndn=m=" + str(tmp_path / "std.rnnn") in command
    assert command[-1] == str(tmp_path / "output.wav")
    assert elapsed_ms >= 0.0
