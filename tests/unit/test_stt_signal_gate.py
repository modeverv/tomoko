from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from server.edge.pipeline.stt_gate import (
    SttAudioFrontend,
    SttSignalGate,
    audio_signal_metrics,
    rnnoise_denoise,
    speech_bandpass,
)
from server.shared.models import SpeechSegment


@pytest.mark.unit
def test_stt_signal_gate_rejects_sparse_low_signal_segment() -> None:
    sample_rate = 16000
    audio = np.zeros(sample_rate * 5, dtype=np.float32)
    t = np.linspace(0, 0.2, int(sample_rate * 0.2), endpoint=False)
    audio[sample_rate : sample_rate + len(t)] = np.sin(2 * np.pi * 440 * t) * 0.035
    now = datetime.now(UTC)
    segment = SpeechSegment(
        audio=audio,
        started_at=now - timedelta(seconds=5),
        ended_at=now,
        device_id="local",
        vad_confidence=0.9,
    )

    decision = SttSignalGate(sample_rate=sample_rate).evaluate_segment(segment)

    assert decision.accepted is False
    assert decision.reason == "low_signal_sparse"
    assert decision.metrics.active_frame_ratio < 0.25


@pytest.mark.unit
def test_stt_signal_gate_accepts_continuous_quiet_speech_like_segment() -> None:
    sample_rate = 16000
    t = np.linspace(0, 1.0, sample_rate, endpoint=False)
    audio = (np.sin(2 * np.pi * 440 * t) * 0.01).astype(np.float32)
    now = datetime.now(UTC)
    segment = SpeechSegment(
        audio=audio,
        started_at=now - timedelta(seconds=1),
        ended_at=now,
        device_id="local",
        vad_confidence=0.9,
    )

    decision = SttSignalGate(sample_rate=sample_rate).evaluate_segment(segment)

    assert decision.accepted is True
    assert decision.reason == "accepted"


@pytest.mark.unit
def test_stt_signal_gate_skips_near_silent_partial_chunk() -> None:
    gate = SttSignalGate(sample_rate=16000)
    chunk = np.ones(512, dtype=np.float32) * 0.001

    assert gate.should_process_partial_chunk(chunk) is False


@pytest.mark.unit
def test_stt_audio_frontend_can_disable_filters() -> None:
    sample_rate = 16000
    now = datetime.now(UTC)
    segment = SpeechSegment(
        audio=np.ones(512, dtype=np.float32) * 0.001,
        started_at=now - timedelta(milliseconds=32),
        ended_at=now,
        device_id="local",
        vad_confidence=0.9,
    )
    frontend = SttAudioFrontend(sample_rate=sample_rate, enabled_filters=())

    decision = frontend.process_segment(segment)

    assert decision.action == "accept"
    assert decision.segment is segment
    assert decision.enabled_filters == ()


@pytest.mark.unit
def test_stt_audio_frontend_can_enable_bandpass_and_signal_gate() -> None:
    sample_rate = 16000
    t = np.linspace(0, 1.0, sample_rate, endpoint=False)
    now = datetime.now(UTC)
    segment = SpeechSegment(
        audio=(np.sin(2 * np.pi * 440 * t) * 0.08).astype(np.float32),
        started_at=now - timedelta(seconds=1),
        ended_at=now,
        device_id="local",
        vad_confidence=0.9,
    )

    decision = SttAudioFrontend(
        sample_rate=sample_rate,
        enabled_filters=("speech_bandpass", "signal_gate"),
    ).process_segment(segment)

    assert decision.action == "accept"
    assert decision.enabled_filters == ("speech_bandpass", "signal_gate")


@pytest.mark.unit
def test_stt_audio_frontend_pends_short_segment_and_merges_next_segment() -> None:
    sample_rate = 16000
    started_at = datetime.now(UTC)
    first = SpeechSegment(
        audio=np.ones(int(sample_rate * 0.2), dtype=np.float32) * 0.08,
        started_at=started_at,
        ended_at=started_at + timedelta(milliseconds=200),
        device_id="local",
        vad_confidence=0.8,
    )
    second = SpeechSegment(
        audio=np.ones(int(sample_rate * 0.6), dtype=np.float32) * 0.08,
        started_at=first.ended_at + timedelta(milliseconds=300),
        ended_at=first.ended_at + timedelta(milliseconds=900),
        device_id="local",
        vad_confidence=0.9,
    )
    frontend = SttAudioFrontend(
        sample_rate=sample_rate,
        enabled_filters=("short_segment_merge", "signal_gate"),
    )

    first_decision = frontend.process_segment(first)
    second_decision = frontend.process_segment(second)

    assert first_decision.action == "pending"
    assert second_decision.action == "accept"
    assert second_decision.segment is not None
    assert len(second_decision.segment.audio) == len(first.audio) + len(second.audio)
    assert second_decision.segment.started_at == first.started_at


@pytest.mark.unit
def test_stt_audio_frontend_keeps_spectral_subtraction_off_without_profile() -> None:
    sample_rate = 16000
    now = datetime.now(UTC)
    audio = np.ones(sample_rate, dtype=np.float32) * 0.08
    segment = SpeechSegment(
        audio=audio,
        started_at=now - timedelta(seconds=1),
        ended_at=now,
        device_id="local",
        vad_confidence=0.9,
    )
    frontend = SttAudioFrontend(
        sample_rate=sample_rate,
        enabled_filters=("spectral_subtraction",),
    )

    decision = frontend.process_segment(segment)

    assert decision.action == "accept"
    assert decision.segment is segment


@pytest.mark.unit
def test_stt_audio_frontend_can_capture_noise_profile_for_spectral_filter() -> None:
    sample_rate = 16000
    now = datetime.now(UTC)
    rng = np.random.default_rng(0)
    noise = rng.normal(0.0, 0.01, sample_rate).astype(np.float32)
    audio = noise + np.ones(sample_rate, dtype=np.float32) * 0.08
    segment = SpeechSegment(
        audio=audio,
        started_at=now - timedelta(seconds=1),
        ended_at=now,
        device_id="local",
        vad_confidence=0.9,
    )
    frontend = SttAudioFrontend(
        sample_rate=sample_rate,
        enabled_filters=("spectral_subtraction",),
    )

    profile = frontend.capture_noise_profile(noise)
    decision = frontend.process_segment(segment)

    assert profile.sample_rate == sample_rate
    assert decision.action == "accept"
    assert decision.segment is not None
    assert decision.segment.audio.shape == segment.audio.shape


@pytest.mark.unit
def test_stt_audio_frontend_leaves_rnnoise_off_when_model_is_missing(tmp_path) -> None:
    sample_rate = 16000
    now = datetime.now(UTC)
    audio = np.ones(sample_rate, dtype=np.float32) * 0.08
    segment = SpeechSegment(
        audio=audio,
        started_at=now - timedelta(seconds=1),
        ended_at=now,
        device_id="local",
        vad_confidence=0.9,
    )
    frontend = SttAudioFrontend(
        sample_rate=sample_rate,
        enabled_filters=("rnnoise",),
        rnnoise_model_path=tmp_path / "missing.rnnn",
    )

    decision = frontend.process_segment(segment)

    assert decision.action == "accept"
    assert decision.segment is segment


@pytest.mark.unit
def test_rnnoise_denoise_invokes_ffmpeg(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command: list[str], *, check: bool) -> None:
        assert command[0] == "ffmpeg"
        assert check is True
        output_path = command[-1]
        input_path = command[command.index("-i") + 1]
        Path(output_path).write_bytes(Path(input_path).read_bytes())

    monkeypatch.setattr("server.edge.pipeline.stt_gate.subprocess.run", fake_run)
    audio = np.ones(512, dtype=np.float32) * 0.1
    model_path = tmp_path / "std.rnnn"
    model_path.write_text("model")

    denoised = rnnoise_denoise(audio, 16000, model_path=model_path)

    assert denoised.shape == audio.shape


@pytest.mark.unit
def test_speech_bandpass_reduces_sub_bass_and_ultrahigh_energy() -> None:
    sample_rate = 16000
    t = np.linspace(0, 1.0, sample_rate, endpoint=False)
    sub_bass = np.sin(2 * np.pi * 40 * t).astype(np.float32)
    voice_band = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    high_noise = np.sin(2 * np.pi * 7800 * t).astype(np.float32)

    filtered_sub_bass = speech_bandpass(sub_bass, sample_rate)
    filtered_voice = speech_bandpass(voice_band, sample_rate)
    filtered_high_noise = speech_bandpass(high_noise, sample_rate)

    assert np.sqrt(np.mean(np.square(filtered_sub_bass))) < 0.2
    assert np.sqrt(np.mean(np.square(filtered_voice))) > 0.5
    assert np.sqrt(np.mean(np.square(filtered_high_noise))) < 0.2


@pytest.mark.unit
def test_audio_signal_metrics_reports_empty_audio_as_silence() -> None:
    metrics = audio_signal_metrics(np.zeros(0, dtype=np.float32), 16000)

    assert metrics.duration_ms == 0.0
    assert metrics.rms_db == -120.0
    assert metrics.peak_db == -120.0
    assert metrics.active_frame_ratio == 0.0
