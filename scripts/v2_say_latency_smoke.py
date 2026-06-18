from __future__ import annotations

import argparse
import asyncio
import json
import math
import struct
import subprocess
import tempfile
import time
import wave
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect


@dataclass(slots=True)
class SayLatencyResult:
    url: str
    text: str
    voice: str
    input_wav: str
    input_duration_ms: float
    chunk_samples: int
    trailing_silence_ms: int
    voice_end_to_transcript_ms: float | None
    voice_end_to_tts_result_ms: float | None
    voice_end_to_first_audio_ms: float | None
    first_audio_bytes: int | None
    final_transcript: str | None
    llm_prompt: str | None
    tts_text: str | None
    model_text: str | None
    event_counts: dict[str, int] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay macOS say audio into Tomoko v2 /ws and measure first audio latency."
    )
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws")
    parser.add_argument("--text", default="トモコ、短く返事して。")
    parser.add_argument("--voice", default="Kyoko")
    parser.add_argument("--chunk-samples", type=int, default=128)
    parser.add_argument("--trailing-silence-ms", type=int, default=2500)
    parser.add_argument("--timeout-sec", type=float, default=45.0)
    parser.add_argument("--output-dir", default="logs")
    return parser.parse_args()


def generate_say_wav(text: str, voice: str, output_dir: Path, stamp: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    wav_path = output_dir / f"say-latency-{stamp}-input.wav"
    with tempfile.TemporaryDirectory() as temp_dir:
        aiff_path = Path(temp_dir) / "input.aiff"
        subprocess.run(
            ["say", "-v", voice, "-o", str(aiff_path), text],
            check=True,
            text=True,
        )
        subprocess.run(
            [
                "afconvert",
                "-f",
                "WAVE",
                "-d",
                "LEI16@16000",
                "-c",
                "1",
                str(aiff_path),
                str(wav_path),
            ],
            check=True,
            text=True,
        )
    return wav_path


def read_wav_float32(path: Path) -> tuple[int, tuple[float, ...]]:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        sample_width = wav.getsampwidth()
        frames = wav.readframes(wav.getnframes())
    if channels != 1:
        raise ValueError(f"expected mono WAV, got {channels} channels")
    if sample_width != 2:
        raise ValueError(f"expected 16-bit WAV, got sample width {sample_width}")
    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
    return sample_rate, tuple(max(-1.0, min(1.0, sample / 32768.0)) for sample in samples)


def pack_float32(samples: tuple[float, ...]) -> bytes:
    if not samples:
        return b""
    return struct.pack(f"<{len(samples)}f", *samples)


async def measure(args: argparse.Namespace, wav_path: Path) -> SayLatencyResult:
    sample_rate, samples = read_wav_float32(wav_path)
    if sample_rate != 16000:
        raise ValueError(f"expected 16000Hz WAV, got {sample_rate}")

    voice_end_at: float | None = None
    first_transcript_at: float | None = None
    first_tts_result_at: float | None = None
    first_audio_at: float | None = None
    first_audio_bytes: int | None = None
    final_transcript: str | None = None
    llm_prompt: str | None = None
    tts_text: str | None = None
    model_text: str | None = None
    events: list[dict[str, Any]] = []
    event_counts: dict[str, int] = {}
    first_audio_seen = asyncio.Event()
    prompt_done = asyncio.Event()
    started_at = time.perf_counter()

    async def receiver(websocket: Any) -> None:
        nonlocal first_transcript_at
        nonlocal first_tts_result_at
        nonlocal first_audio_at
        nonlocal first_audio_bytes
        nonlocal final_transcript
        nonlocal llm_prompt
        nonlocal tts_text
        nonlocal model_text

        async for message in websocket:
            now = time.perf_counter()
            if isinstance(message, bytes):
                if first_audio_at is None:
                    first_audio_at = now
                    first_audio_bytes = len(message)
                    first_audio_seen.set()
                event_counts["binary_audio"] = event_counts.get("binary_audio", 0) + 1
                continue

            payload = json.loads(message)
            event_type = str(payload.get("type", "unknown"))
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
            if event_type != "debug_marker":
                events.append({"elapsed_ms": (now - started_at) * 1000.0, "payload": payload})
            if event_type == "transcript" and payload.get("is_final"):
                final_transcript = str(payload.get("text", ""))
                if first_transcript_at is None:
                    first_transcript_at = now
            elif event_type == "llm_prompt":
                llm_prompt = str(payload.get("prompt_text", ""))
            elif event_type == "model_complete":
                model_text = str(payload.get("text", ""))
            elif event_type == "tts_result":
                tts_text = str(payload.get("text", ""))
                if first_tts_result_at is None:
                    first_tts_result_at = now
            elif event_type == "prompt_complete":
                prompt_done.set()

    async with connect(args.url, max_size=None) as websocket:
        receive_task = asyncio.create_task(receiver(websocket))

        for offset in range(0, len(samples), args.chunk_samples):
            chunk = samples[offset : offset + args.chunk_samples]
            await websocket.send(pack_float32(chunk))
            await asyncio.sleep(len(chunk) / sample_rate)
        voice_end_at = time.perf_counter()

        silence_samples = int(sample_rate * args.trailing_silence_ms / 1000)
        silence_chunk = (0.0,) * args.chunk_samples
        silence_chunks = math.ceil(silence_samples / args.chunk_samples)
        for _ in range(silence_chunks):
            if first_audio_seen.is_set():
                break
            await websocket.send(pack_float32(silence_chunk))
            await asyncio.sleep(args.chunk_samples / sample_rate)

        try:
            await asyncio.wait_for(first_audio_seen.wait(), timeout=args.timeout_sec)
        finally:
            try:
                await asyncio.wait_for(prompt_done.wait(), timeout=5.0)
            except TimeoutError:
                pass
            await websocket.close()
            receive_task.cancel()
            try:
                await receive_task
            except asyncio.CancelledError:
                pass

    def delta_ms(value: float | None) -> float | None:
        if value is None or voice_end_at is None:
            return None
        return (value - voice_end_at) * 1000.0

    return SayLatencyResult(
        url=args.url,
        text=args.text,
        voice=args.voice,
        input_wav=str(wav_path),
        input_duration_ms=len(samples) / sample_rate * 1000.0,
        chunk_samples=args.chunk_samples,
        trailing_silence_ms=args.trailing_silence_ms,
        voice_end_to_transcript_ms=delta_ms(first_transcript_at),
        voice_end_to_tts_result_ms=delta_ms(first_tts_result_at),
        voice_end_to_first_audio_ms=delta_ms(first_audio_at),
        first_audio_bytes=first_audio_bytes,
        final_transcript=final_transcript,
        llm_prompt=llm_prompt,
        tts_text=tts_text,
        model_text=model_text,
        event_counts=event_counts,
        events=events,
    )


async def async_main() -> None:
    args = parse_args()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir)
    wav_path = generate_say_wav(args.text, args.voice, output_dir, stamp)
    result = await measure(args, wav_path)
    payload = asdict(result)
    output_path = output_dir / f"say-latency-{stamp}.json"
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"wrote {output_path}")


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
