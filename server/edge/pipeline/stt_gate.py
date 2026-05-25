from __future__ import annotations

import math
import os
import subprocess
import tempfile
import wave
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import numpy as np

from server.shared.models import SpeechSegment

SttAudioFilterName = Literal[
    "signal_gate",
    "speech_bandpass",
    "rnnoise",
    "short_segment_merge",
    "spectral_subtraction",
]
SttAudioFrontendAction = Literal["accept", "reject", "pending"]


@dataclass(frozen=True, slots=True)
class AudioSignalMetrics:
    duration_ms: float
    rms_db: float
    peak_db: float
    active_frame_ratio: float


@dataclass(frozen=True, slots=True)
class SttGateDecision:
    accepted: bool
    reason: str
    metrics: AudioSignalMetrics


@dataclass(frozen=True, slots=True)
class NoiseProfile:
    sample_rate: int
    fft_size: int
    hop_size: int
    median_magnitude: np.ndarray
    metrics: AudioSignalMetrics


@dataclass(frozen=True, slots=True)
class SttAudioFrontendDecision:
    action: SttAudioFrontendAction
    reason: str
    segment: SpeechSegment | None
    metrics: AudioSignalMetrics
    enabled_filters: tuple[SttAudioFilterName, ...]

    @property
    def accepted(self) -> bool:
        return self.action == "accept"


class SttAudioFrontend:
    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        enabled_filters: tuple[SttAudioFilterName, ...] = ("signal_gate",),
        signal_gate: SttSignalGate | None = None,
        noise_profile: NoiseProfile | None = None,
        rnnoise_model_path: Path | None = None,
        short_pending_max_ms: float = 450.0,
        short_merge_gap_ms: float = 650.0,
    ) -> None:
        self.sample_rate = sample_rate
        self.enabled_filters = enabled_filters
        self.signal_gate = signal_gate or SttSignalGate(sample_rate=sample_rate)
        self.noise_profile = noise_profile
        self.rnnoise_model_path = rnnoise_model_path or Path("work/rnnoise-models/std.rnnn")
        self.short_pending_max_ms = short_pending_max_ms
        self.short_merge_gap_ms = short_merge_gap_ms
        self._pending_short_segment: SpeechSegment | None = None

    def capture_noise_profile(self, audio: np.ndarray) -> NoiseProfile:
        profile = build_noise_profile(audio, self.sample_rate)
        self.noise_profile = profile
        return profile

    def clear_pending(self) -> None:
        self._pending_short_segment = None

    def process_segment(self, segment: SpeechSegment) -> SttAudioFrontendDecision:
        current = segment
        pending_result, current = self._apply_short_segment_merge(current)
        if pending_result is not None:
            return pending_result
        current = self._apply_speech_bandpass(current)
        current = self._apply_rnnoise(current)
        current = self._apply_spectral_subtraction(current)
        metrics = audio_signal_metrics(current.audio, self.sample_rate)
        if "signal_gate" in self.enabled_filters:
            gate_decision = self.signal_gate.evaluate_segment(current)
            if not gate_decision.accepted:
                return SttAudioFrontendDecision(
                    action="reject",
                    reason=gate_decision.reason,
                    segment=None,
                    metrics=gate_decision.metrics,
                    enabled_filters=self.enabled_filters,
                )
            metrics = gate_decision.metrics
        return SttAudioFrontendDecision(
            action="accept",
            reason="accepted",
            segment=current,
            metrics=metrics,
            enabled_filters=self.enabled_filters,
        )

    def should_process_partial_chunk(self, chunk: np.ndarray) -> bool:
        if "signal_gate" not in self.enabled_filters:
            return True
        return self.signal_gate.should_process_partial_chunk(chunk)

    def _apply_short_segment_merge(
        self,
        segment: SpeechSegment,
    ) -> tuple[SttAudioFrontendDecision | None, SpeechSegment]:
        if "short_segment_merge" not in self.enabled_filters:
            self._pending_short_segment = None
            return None, segment

        current = segment
        pending = self._pending_short_segment
        if pending is not None:
            gap_ms = (segment.started_at - pending.ended_at).total_seconds() * 1000
            if 0 <= gap_ms <= self.short_merge_gap_ms:
                current = merge_speech_segments(pending, segment)
                self._pending_short_segment = None
                return None, current
            self.clear_pending()

        metrics = audio_signal_metrics(current.audio, self.sample_rate)
        if metrics.duration_ms <= self.short_pending_max_ms:
            self._pending_short_segment = current
            return SttAudioFrontendDecision(
                action="pending",
                reason="short_segment_pending",
                segment=None,
                metrics=metrics,
                enabled_filters=self.enabled_filters,
            ), current
        return None, current

    def _apply_speech_bandpass(self, segment: SpeechSegment) -> SpeechSegment:
        if "speech_bandpass" not in self.enabled_filters:
            return segment
        filtered = speech_bandpass(segment.audio, self.sample_rate)
        return replace(segment, audio=filtered)

    def _apply_spectral_subtraction(self, segment: SpeechSegment) -> SpeechSegment:
        if "spectral_subtraction" not in self.enabled_filters:
            return segment
        if self.noise_profile is None:
            return segment
        filtered = spectral_subtract(segment.audio, self.noise_profile)
        return replace(segment, audio=filtered)

    def _apply_rnnoise(self, segment: SpeechSegment) -> SpeechSegment:
        if "rnnoise" not in self.enabled_filters:
            return segment
        if not self.rnnoise_model_path.exists():
            return segment
        filtered = rnnoise_denoise(
            segment.audio,
            self.sample_rate,
            model_path=self.rnnoise_model_path,
        )
        return replace(segment, audio=filtered)


class SttSignalGate:
    """Cheap pre-STT gate to avoid sending unusable audio to Whisper."""

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        frame_ms: int = 32,
        active_threshold_db: float = -55.0,
        min_duration_ms: float = 80.0,
        min_peak_db: float = -60.0,
        min_rms_db: float = -52.0,
        low_signal_rms_db: float = -45.0,
        low_signal_max_active_ratio: float = 0.25,
        partial_min_peak_db: float = -45.0,
        partial_min_rms_db: float = -55.0,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.active_threshold_db = active_threshold_db
        self.min_duration_ms = min_duration_ms
        self.min_peak_db = min_peak_db
        self.min_rms_db = min_rms_db
        self.low_signal_rms_db = low_signal_rms_db
        self.low_signal_max_active_ratio = low_signal_max_active_ratio
        self.partial_min_peak_db = partial_min_peak_db
        self.partial_min_rms_db = partial_min_rms_db

    def evaluate_segment(self, segment: SpeechSegment) -> SttGateDecision:
        metrics = audio_signal_metrics(
            segment.audio,
            self.sample_rate,
            frame_ms=self.frame_ms,
            active_threshold_db=self.active_threshold_db,
        )
        reason = self._reject_reason(metrics)
        return SttGateDecision(
            accepted=reason is None,
            reason=reason or "accepted",
            metrics=metrics,
        )

    def should_process_partial_chunk(self, chunk: np.ndarray) -> bool:
        metrics = audio_signal_metrics(
            chunk,
            self.sample_rate,
            frame_ms=self.frame_ms,
            active_threshold_db=self.active_threshold_db,
        )
        if metrics.peak_db < self.partial_min_peak_db:
            return False
        return metrics.rms_db >= self.partial_min_rms_db

    def _reject_reason(self, metrics: AudioSignalMetrics) -> str | None:
        if metrics.duration_ms < self.min_duration_ms:
            return "too_short"
        if metrics.peak_db < self.min_peak_db:
            return "peak_too_low"
        if metrics.rms_db < self.min_rms_db:
            return "rms_too_low"
        if (
            metrics.rms_db < self.low_signal_rms_db
            and metrics.active_frame_ratio < self.low_signal_max_active_ratio
        ):
            return "low_signal_sparse"
        return None


def merge_speech_segments(
    first: SpeechSegment,
    second: SpeechSegment,
) -> SpeechSegment:
    return SpeechSegment(
        audio=np.concatenate([first.audio, second.audio]),
        started_at=first.started_at,
        ended_at=second.ended_at,
        device_id=second.device_id,
        vad_confidence=max(first.vad_confidence, second.vad_confidence),
    )


def build_noise_profile(
    audio: np.ndarray,
    sample_rate: int,
    *,
    fft_size: int = 512,
    hop_size: int = 128,
) -> NoiseProfile:
    frames = _frame_audio(audio, fft_size, hop_size)
    window = np.hanning(fft_size).astype(np.float32)
    magnitudes = np.abs(np.fft.rfft(frames * window[None, :], axis=1))
    return NoiseProfile(
        sample_rate=sample_rate,
        fft_size=fft_size,
        hop_size=hop_size,
        median_magnitude=np.median(magnitudes, axis=0),
        metrics=audio_signal_metrics(audio, sample_rate),
    )


def spectral_subtract(
    audio: np.ndarray,
    profile: NoiseProfile,
    *,
    over_subtract: float = 1.5,
    min_gain: float = 0.15,
) -> np.ndarray:
    if audio.size == 0:
        return audio.copy()
    frames = _frame_audio(audio, profile.fft_size, profile.hop_size)
    window = np.hanning(profile.fft_size).astype(np.float32)
    output = np.zeros(
        (frames.shape[0] - 1) * profile.hop_size + profile.fft_size,
        dtype=np.float32,
    )
    norm = np.zeros_like(output)
    for index, frame in enumerate(frames):
        spectrum = np.fft.rfft(frame * window)
        magnitude = np.abs(spectrum)
        gain = np.maximum(
            min_gain,
            (magnitude - over_subtract * profile.median_magnitude)
            / np.maximum(magnitude, 1e-8),
        )
        filtered = np.fft.irfft(spectrum * gain, n=profile.fft_size).astype(np.float32)
        start = index * profile.hop_size
        output[start : start + profile.fft_size] += filtered * window
        norm[start : start + profile.fft_size] += window * window
    valid = norm > 1e-4
    output[valid] /= norm[valid]
    output[~valid] = 0.0
    return np.clip(output[: len(audio)], -1.0, 1.0)


def speech_bandpass(
    audio: np.ndarray,
    sample_rate: int,
    *,
    highpass_hz: float = 100.0,
    lowpass_hz: float = 7200.0,
    highpass_transition_hz: float = 40.0,
    lowpass_transition_hz: float = 800.0,
) -> np.ndarray:
    if audio.size == 0:
        return audio.copy()
    samples = audio.astype(np.float32, copy=False)
    nyquist = sample_rate / 2
    spectrum = np.fft.rfft(samples)
    frequencies = np.fft.rfftfreq(len(samples), d=1 / sample_rate)
    gain = np.ones_like(frequencies, dtype=np.float32)

    high_start = max(0.0, highpass_hz - highpass_transition_hz)
    gain[frequencies <= high_start] = 0.0
    high_transition = (frequencies > high_start) & (frequencies < highpass_hz)
    if high_transition.any():
        ratio = (frequencies[high_transition] - high_start) / max(
            highpass_hz - high_start,
            1e-9,
        )
        gain[high_transition] *= _smoothstep(ratio)

    if lowpass_hz < nyquist:
        low_end = min(nyquist, lowpass_hz + lowpass_transition_hz)
        gain[frequencies >= low_end] = 0.0
        low_transition = (frequencies > lowpass_hz) & (frequencies < low_end)
        if low_transition.any():
            ratio = (frequencies[low_transition] - lowpass_hz) / max(
                low_end - lowpass_hz,
                1e-9,
            )
            gain[low_transition] *= 1.0 - _smoothstep(ratio)

    filtered = np.fft.irfft(spectrum * gain, n=len(samples)).astype(np.float32)
    return np.clip(filtered, -1.0, 1.0)


def rnnoise_denoise(
    audio: np.ndarray,
    sample_rate: int,
    *,
    model_path: Path,
) -> np.ndarray:
    if audio.size == 0:
        return audio.copy()
    input_path = _write_temp_wav(audio, sample_rate)
    output_path = input_path.with_name(input_path.stem + "-rnnoise.wav")
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(input_path),
                "-af",
                f"arnndn=m={model_path}",
                "-ac",
                "1",
                "-ar",
                str(sample_rate),
                str(output_path),
            ],
            check=True,
        )
        filtered, _sample_rate = _read_wav(output_path)
        return filtered
    finally:
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)


def audio_signal_metrics(
    audio: np.ndarray,
    sample_rate: int,
    *,
    frame_ms: int = 32,
    active_threshold_db: float = -55.0,
) -> AudioSignalMetrics:
    if audio.size == 0:
        return AudioSignalMetrics(
            duration_ms=0.0,
            rms_db=-120.0,
            peak_db=-120.0,
            active_frame_ratio=0.0,
        )

    samples = audio.astype(np.float32, copy=False)
    frame_size = max(1, int(sample_rate * frame_ms / 1000))
    frame_count = math.ceil(len(samples) / frame_size)
    padded = np.pad(samples, (0, frame_count * frame_size - len(samples)))
    frames = padded.reshape(frame_count, frame_size)
    frame_rms = np.sqrt(np.mean(np.square(frames), axis=1))
    frame_db = np.array([_db_from_rms(float(value)) for value in frame_rms])
    return AudioSignalMetrics(
        duration_ms=len(samples) * 1000 / sample_rate,
        rms_db=_db_from_rms(float(np.sqrt(np.mean(np.square(samples))))),
        peak_db=_db_from_rms(float(np.max(np.abs(samples)))),
        active_frame_ratio=float(np.mean(frame_db >= active_threshold_db)),
    )


def _db_from_rms(rms: float) -> float:
    if rms <= 0.0:
        return -120.0
    return 20.0 * math.log10(rms)


def _smoothstep(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, 0.0, 1.0)
    return clipped * clipped * (3.0 - 2.0 * clipped)


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio, sample_rate


def _write_temp_wav(audio: np.ndarray, sample_rate: int) -> Path:
    samples = np.clip(audio.astype(np.float32, copy=False), -1.0, 1.0)
    pcm = (samples * 32767.0).astype(np.int16)
    fd, path_name = tempfile.mkstemp(prefix="tomoko-rnnoise-", suffix=".wav")
    os.close(fd)
    path = Path(path_name)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())
    return path


def _frame_audio(audio: np.ndarray, fft_size: int, hop_size: int) -> np.ndarray:
    if audio.size == 0:
        return np.zeros((1, fft_size), dtype=np.float32)
    frame_count = max(1, math.ceil((len(audio) - fft_size) / hop_size) + 1)
    total = (frame_count - 1) * hop_size + fft_size
    padded = np.pad(
        audio.astype(np.float32, copy=False),
        (0, max(0, total - len(audio))),
    )
    return np.stack(
        [
            padded[index * hop_size : index * hop_size + fft_size]
            for index in range(frame_count)
        ]
    )
