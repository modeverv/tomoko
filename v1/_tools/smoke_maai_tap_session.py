from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.edge.pipeline.vad import VADProcessor  # noqa: E402
from server.gateway.maai_backchannel import (  # noqa: E402
    MaaiBackchannelConfig,
    MaaiBackchannelTap,
)
from server.gateway.turn_taking.barge_in import BargeInDetector  # noqa: E402
from server.session import TomoroSession  # noqa: E402
from server.shared.inference.tts.say import SayBackend  # noqa: E402
from server.shared.models import BackchannelSuggestion  # noqa: E402


class QuietVad:
    def process_chunk(self, chunk: np.ndarray) -> float:
        del chunk
        return 0.0


@dataclass
class RecordingInteractionTap:
    user_chunks: list[int] = field(default_factory=list)
    tomoko_chunks: list[int] = field(default_factory=list)

    def observe_user_audio(self, chunk: np.ndarray, *, observed_at: datetime) -> None:
        del observed_at
        self.user_chunks.append(int(chunk.size))

    def observe_tomoko_audio(self, chunk: bytes, *, observed_at: datetime) -> None:
        del observed_at
        self.tomoko_chunks.append(len(chunk))


def _sine_chunks(
    *,
    seconds: float,
    sample_rate: int = 16000,
    chunk_size: int = 512,
    frequency_hz: float = 440.0,
) -> list[np.ndarray]:
    if seconds <= 0:
        return []
    sample_count = max(1, int(sample_rate * seconds))
    index = np.arange(sample_count, dtype=np.float32)
    audio = 0.08 * np.sin(2 * math.pi * frequency_hz * index / sample_rate)
    return [
        np.asarray(audio[start : start + chunk_size], dtype=np.float32)
        for start in range(0, sample_count, chunk_size)
    ]


async def run_smoke(
    *,
    text: str,
    style: str,
    voice: str,
    user_sine_sec: float = 0.0,
    use_maai: bool = False,
    output_path: Path | None = None,
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    sent_audio: list[bytes] = []
    suggestions: list[BackchannelSuggestion] = []
    tap = (
        MaaiBackchannelTap(
            config=MaaiBackchannelConfig(),
            suggestion_callback=suggestions.append,
        )
        if use_maai
        else RecordingInteractionTap()
    )
    if isinstance(tap, MaaiBackchannelTap):
        await tap.start()
    session = TomoroSession(
        vad_processor=VADProcessor(vad=QuietVad(), silence_ms=400),
        send_event=events.append,
        send_audio=sent_audio.append,
        tts_backend=SayBackend(voice=voice),
        barge_in_detector=BargeInDetector(),
        audio_interaction_tap=tap,
    )

    try:
        for chunk in _sine_chunks(seconds=user_sine_sec):
            await session.process_audio_chunk(chunk.tobytes())

        await session._flush_tts_text(text, style=style)
        await session._send_reserved_audio_end()
    finally:
        if isinstance(tap, MaaiBackchannelTap):
            await tap.stop()

    tomoko_tap_chunks = (
        len(tap.tomoko_chunks)
        if isinstance(tap, RecordingInteractionTap)
        else len(sent_audio)
    )
    tomoko_tap_bytes = (
        sum(tap.tomoko_chunks)
        if isinstance(tap, RecordingInteractionTap)
        else sum(len(chunk) for chunk in sent_audio)
    )
    user_tap_chunks = (
        len(tap.user_chunks)
        if isinstance(tap, RecordingInteractionTap)
        else len(_sine_chunks(seconds=user_sine_sec))
    )
    user_tap_samples = (
        sum(tap.user_chunks)
        if isinstance(tap, RecordingInteractionTap)
        else sum(len(chunk) for chunk in _sine_chunks(seconds=user_sine_sec))
    )

    summary = {
        "maai_enabled": use_maai,
        "say_invoked": bool(sent_audio),
        "text": text,
        "style": style,
        "voice": voice,
        "sent_audio_chunks": len(sent_audio),
        "sent_audio_bytes": sum(len(chunk) for chunk in sent_audio),
        "tomoko_tap_chunks": tomoko_tap_chunks,
        "tomoko_tap_bytes": tomoko_tap_bytes,
        "user_tap_chunks": user_tap_chunks,
        "user_tap_samples": user_tap_samples,
        "suggestions": [suggestion.to_json() for suggestion in suggestions],
        "events": events,
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run TomoroSession without a browser and feed macOS say audio through "
            "the optional MaAI interaction tap."
        )
    )
    parser.add_argument("--text", default="うん、聞こえるよ。")
    parser.add_argument("--style", default="neutral")
    parser.add_argument("--voice", default="Kyoko")
    parser.add_argument("--user-sine-sec", type=float, default=0.25)
    parser.add_argument(
        "--use-maai",
        action="store_true",
        help="Use the real MaAI adapter instead of the recording tap.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("logs/maai-tap-session-smoke.json"),
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    summary = await run_smoke(
        text=args.text,
        style=args.style,
        voice=args.voice,
        user_sine_sec=args.user_sine_sec,
        use_maai=args.use_maai,
        output_path=args.output,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
