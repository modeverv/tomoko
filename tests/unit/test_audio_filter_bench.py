from __future__ import annotations

import numpy as np
import pytest

from _tools.bench_audio_filters import (
    audio_metrics,
    frame_rms_gate,
    hard_segment_gate,
    spectral_gate,
)


@pytest.mark.unit
def test_frame_rms_gate_removes_quiet_frames() -> None:
    sample_rate = 16000
    quiet = np.zeros(512, dtype=np.float32)
    speech = np.ones(512, dtype=np.float32) * 0.1
    audio = np.concatenate([quiet, speech, quiet])

    filtered, kept_ratio = frame_rms_gate(
        audio,
        sample_rate,
        threshold_db=-30.0,
        frame_ms=32,
        hangover_ms=0,
    )

    assert kept_ratio == pytest.approx(1 / 3)
    assert np.allclose(filtered[:512], 0.0)
    assert np.max(np.abs(filtered[512:1024])) > 0.0
    assert np.allclose(filtered[1024:], 0.0)


@pytest.mark.unit
def test_hard_segment_gate_rejects_low_rms_segment() -> None:
    sample_rate = 8000
    audio = np.ones(sample_rate, dtype=np.float32) * 0.001

    filtered, kept_ratio = hard_segment_gate(audio, sample_rate, -50.0)

    assert kept_ratio == 0.0
    assert np.allclose(filtered, 0.0)


@pytest.mark.unit
def test_spectral_gate_preserves_shape_and_reduces_noise_floor() -> None:
    sample_rate = 16000
    rng = np.random.default_rng(0)
    noise = rng.normal(0.0, 0.01, sample_rate).astype(np.float32)
    speech_like = noise + (np.sin(np.linspace(0, 50, sample_rate)) * 0.05).astype(
        np.float32
    )

    filtered, mean_gain = spectral_gate(speech_like, noise, sample_rate)

    assert filtered.shape == speech_like.shape
    assert 0.0 < mean_gain <= 1.0
    assert audio_metrics(filtered, sample_rate).peak_db <= 0.0
