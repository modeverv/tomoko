from __future__ import annotations

import argparse
import asyncio
import json
import sys
import wave
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from _tools.smoke_maai_dialogue import (  # noqa: E402
    RawScoreMaaiTap,
    SmokeBackchannelTTS,
    _frame,
    _gesture_state,
    _json_safe,
)
from server.gateway.gesture_audio import GestureAudioEmitter  # noqa: E402
from server.gateway.maai_backchannel import (  # noqa: E402
    MAAI_FRAME_SIZE,
    MAAI_SAMPLE_RATE,
    MaaiBackchannelConfig,
)
from server.shared.models import BackchannelSuggestion  # noqa: E402


@dataclass(frozen=True)
class MaterialTimeline:
    source_path: Path
    source_sample_rate: int
    source_channels: int
    user_audio: np.ndarray
    tomoko_audio: np.ndarray
    duration_sec: float


async def run_material_smoke(
    *,
    input_path: Path,
    maai_module: Any | None = None,
    realtime_scale: float = 1.0,
    wait_after_sec: float = 1.0,
    output_path: Path | None = None,
    speech_rms_threshold: float = 0.01,
    start_sec: float = 0.0,
    duration_sec: float | None = None,
    swap_channels: bool = False,
) -> dict[str, Any]:
    source_timeline = decode_stereo_wav_16k(input_path, swap_channels=swap_channels)
    timeline = slice_timeline(
        source_timeline,
        start_sec=start_sec,
        duration_sec=duration_sec,
    )
    raw_scores: list[dict[str, Any]] = []
    suggestions: list[BackchannelSuggestion] = []
    tap = RawScoreMaaiTap(
        config=MaaiBackchannelConfig(),
        raw_scores=raw_scores,
        suggestions=suggestions,
        maai_module=maai_module,
    )

    await tap.start()
    try:
        frames_sent = await feed_material_timeline(
            tap,
            timeline,
            realtime_scale=realtime_scale,
        )
        if wait_after_sec > 0:
            await asyncio.sleep(wait_after_sec)
    finally:
        await tap.stop()

    session_releases = await simulate_material_session_releases(
        suggestions=suggestions,
        raw_scores=raw_scores,
        timeline=timeline,
        speech_rms_threshold=speech_rms_threshold,
    )
    summary = {
        "maai_enabled": True,
        "source_path": str(input_path),
        "source_sample_rate": timeline.source_sample_rate,
        "source_channels": timeline.source_channels,
        "source_start_sec": start_sec,
        "source_duration_sec": source_timeline.duration_sec,
        "sample_rate": MAAI_SAMPLE_RATE,
        "frame_size": MAAI_FRAME_SIZE,
        "frame_sec": MAAI_FRAME_SIZE / MAAI_SAMPLE_RATE,
        "duration_sec": timeline.duration_sec,
        "frames_sent": frames_sent,
        "raw_score_count": len(raw_scores),
        "max_p_bc_react": max(
            (score["p_bc_react"] or 0.0 for score in raw_scores),
            default=0.0,
        ),
        "max_p_bc_emo": max(
            (score["p_bc_emo"] or 0.0 for score in raw_scores),
            default=0.0,
        ),
        "channel_mapping": {
            "ch1": "tomoko" if swap_channels else "user",
            "ch2": "user" if swap_channels else "tomoko",
        },
        "speech_rms_threshold": speech_rms_threshold,
        "raw_scores": raw_scores,
        "suggestions": [suggestion.to_json() for suggestion in suggestions],
        "session_releases": session_releases,
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return summary


def decode_stereo_wav_16k(path: Path, *, swap_channels: bool = False) -> MaterialTimeline:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        frame_count = wav.getnframes()
        raw = wav.readframes(frame_count)
    if channels < 2:
        raise ValueError(f"{path} must be stereo or more channels")
    if sample_width == 2:
        audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sample_width == 4:
        audio = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"unsupported sample width: {sample_width}")
    audio = audio.reshape(-1, channels)
    if swap_channels:
        user_audio = audio[:, 1]
        tomoko_audio = audio[:, 0]
    else:
        user_audio = audio[:, 0]
        tomoko_audio = audio[:, 1]
    if sample_rate != MAAI_SAMPLE_RATE:
        user_audio = _resample_linear(user_audio, sample_rate, MAAI_SAMPLE_RATE)
        tomoko_audio = _resample_linear(tomoko_audio, sample_rate, MAAI_SAMPLE_RATE)
    length = max(user_audio.size, tomoko_audio.size)
    user_audio = _pad_or_trim(user_audio, length)
    tomoko_audio = _pad_or_trim(tomoko_audio, length)
    return MaterialTimeline(
        source_path=path,
        source_sample_rate=sample_rate,
        source_channels=channels,
        user_audio=user_audio,
        tomoko_audio=tomoko_audio,
        duration_sec=length / MAAI_SAMPLE_RATE,
    )


def slice_timeline(
    timeline: MaterialTimeline,
    *,
    start_sec: float = 0.0,
    duration_sec: float | None = None,
) -> MaterialTimeline:
    start = max(0, int(round(start_sec * MAAI_SAMPLE_RATE)))
    if duration_sec is None:
        end = max(timeline.user_audio.size, timeline.tomoko_audio.size)
    else:
        end = start + max(0, int(round(duration_sec * MAAI_SAMPLE_RATE)))
    end = min(end, max(timeline.user_audio.size, timeline.tomoko_audio.size))
    user_audio = _pad_or_trim(timeline.user_audio[start:end], max(0, end - start))
    tomoko_audio = _pad_or_trim(timeline.tomoko_audio[start:end], max(0, end - start))
    return MaterialTimeline(
        source_path=timeline.source_path,
        source_sample_rate=timeline.source_sample_rate,
        source_channels=timeline.source_channels,
        user_audio=user_audio,
        tomoko_audio=tomoko_audio,
        duration_sec=user_audio.size / MAAI_SAMPLE_RATE,
    )


async def feed_material_timeline(
    tap: RawScoreMaaiTap,
    timeline: MaterialTimeline,
    *,
    realtime_scale: float,
) -> int:
    frames_sent = 0
    frame_sec = MAAI_FRAME_SIZE / MAAI_SAMPLE_RATE
    total = max(timeline.user_audio.size, timeline.tomoko_audio.size)
    for start in range(0, total, MAAI_FRAME_SIZE):
        tap.observe_duplex_audio(
            user_chunk=_frame(timeline.user_audio, start),
            tomoko_chunk=_frame(timeline.tomoko_audio, start),
            observed_at=datetime.now(UTC),
        )
        frames_sent += 1
        if realtime_scale > 0:
            await asyncio.sleep(frame_sec * realtime_scale)
    return frames_sent


async def simulate_material_session_releases(
    *,
    suggestions: list[BackchannelSuggestion],
    raw_scores: list[dict[str, Any]],
    timeline: MaterialTimeline,
    speech_rms_threshold: float,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    sent_audio: list[bytes] = []
    tts = SmokeBackchannelTTS()
    releases: list[dict[str, Any]] = []
    previous_event_count = 0
    previous_audio_count = 0
    previous_tts_count = 0
    current_user_speaking = False
    current_tomoko_speaking = False
    emitter = GestureAudioEmitter(
        state_provider=lambda: _gesture_state(
            user_speaking=current_user_speaking,
            tomoko_speaking=current_tomoko_speaking,
        ),
        send_event=events.append,
        send_audio=sent_audio.append,
        tts_backend=tts,
        react_utterances=("うん",),
    )
    for suggestion in suggestions:
        observed_sec = _suggestion_observed_sec(suggestion, raw_scores)
        user_rms = _window_rms(timeline.user_audio, observed_sec)
        tomoko_rms = _window_rms(timeline.tomoko_audio, observed_sec)
        user_speaking = user_rms >= speech_rms_threshold
        tomoko_speaking = tomoko_rms >= speech_rms_threshold
        current_user_speaking = user_speaking
        current_tomoko_speaking = tomoko_speaking
        await emitter.release_backchannel(suggestion)
        new_events = events[previous_event_count:]
        new_audio = sent_audio[previous_audio_count:]
        new_tts_inputs = tts.inputs[previous_tts_count:]
        previous_event_count = len(events)
        previous_audio_count = len(sent_audio)
        previous_tts_count = len(tts.inputs)
        releases.append(
            {
                "suggestion": {
                    **suggestion.to_json(),
                    "observed_sec": observed_sec,
                },
                "timeline": {
                    "user_speaking": user_speaking,
                    "tomoko_speaking": tomoko_speaking,
                    "user_rms": user_rms,
                    "tomoko_rms": tomoko_rms,
                },
                "emissions": [
                    {
                        "type": event.get("type"),
                        "payload": _json_safe(
                            {key: value for key, value in event.items() if key != "type"}
                        ),
                    }
                    for event in new_events
                    if event.get("type") in {"backchannel_released", "backchannel_skipped"}
                ],
                "audio_chunks": len(new_audio),
                "audio_bytes": sum(len(chunk) for chunk in new_audio),
                "tts_inputs": [
                    {
                        "text": item.text,
                        "style": item.style,
                        "voice": item.voice,
                    }
                    for item in new_tts_inputs
                ],
                "reply_done_controls": [
                    event.get("control")
                    for event in new_events
                    if event.get("type") == "reply_done"
                ],
            }
        )
    return releases


def _suggestion_observed_sec(
    suggestion: BackchannelSuggestion,
    raw_scores: list[dict[str, Any]],
) -> float:
    observed_at = suggestion.observed_at.isoformat()
    for score in raw_scores:
        if score.get("observed_at") == observed_at:
            return float(score.get("observed_sec") or 0.0)
    return 0.0


def _window_rms(
    audio: np.ndarray,
    at_sec: float,
    *,
    window_sec: float = 0.5,
) -> float:
    center = int(round(at_sec * MAAI_SAMPLE_RATE))
    half = int(round(window_sec * MAAI_SAMPLE_RATE / 2))
    start = max(0, center - half)
    end = min(audio.size, center + half)
    if end <= start:
        return 0.0
    window = np.asarray(audio[start:end], dtype=np.float32)
    return float(np.sqrt(np.mean(window * window)))


def _pad_or_trim(audio: np.ndarray, length: int) -> np.ndarray:
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size >= length:
        return audio[:length]
    padded = np.zeros(length, dtype=np.float32)
    padded[: audio.size] = audio
    return padded


def _resample_linear(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if audio.size == 0 or source_rate <= 0 or target_rate <= 0:
        return np.empty(0, dtype=np.float32)
    if source_rate == target_rate:
        return np.asarray(audio, dtype=np.float32)
    duration = audio.size / source_rate
    target_size = max(1, int(round(duration * target_rate)))
    source_x = np.linspace(0.0, duration, num=audio.size, endpoint=False)
    target_x = np.linspace(0.0, duration, num=target_size, endpoint=False)
    return np.interp(target_x, source_x, audio).astype(np.float32)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Feed a stereo material WAV to MaAI bc_2type and dump raw scores, "
            "suggestions, and TomoroSession release decisions as JSON."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("_tools/materials/maai.wav"),
    )
    parser.add_argument("--realtime-scale", type=float, default=1.0)
    parser.add_argument("--wait-after-sec", type=float, default=1.0)
    parser.add_argument("--speech-rms-threshold", type=float, default=0.01)
    parser.add_argument("--start-sec", type=float, default=0.0)
    parser.add_argument("--duration-sec", type=float, default=None)
    parser.add_argument("--swap-channels", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("logs/maai-material-smoke.json"),
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    summary = await run_material_smoke(
        input_path=args.input,
        realtime_scale=args.realtime_scale,
        wait_after_sec=args.wait_after_sec,
        output_path=args.output,
        speech_rms_threshold=args.speech_rms_threshold,
        start_sec=args.start_sec,
        duration_sec=args.duration_sec,
        swap_channels=args.swap_channels,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
