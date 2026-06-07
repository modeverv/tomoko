from __future__ import annotations

import argparse
import asyncio
import json
import math
import subprocess
import sys
import tempfile
import time
import wave
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 512
DEFAULT_TEXT = "トモコ、短く返事して。"


@dataclass(frozen=True)
class AudioChunk:
    samples: np.ndarray
    is_voice: bool

    def to_wire_bytes(self) -> bytes:
        return np.asarray(self.samples, dtype="<f4").tobytes()


@dataclass
class WsLatencyRecorder:
    started_at: float
    timestamps: dict[str, float] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    binary_audio_chunks: int = 0
    binary_audio_bytes: int = 0
    reply_text: str = ""
    transcript_text: str | None = None
    audio_turn_id: str | None = None

    def mark(self, name: str, now: float | None = None) -> bool:
        if name in self.timestamps:
            return False
        self.timestamps[name] = self.started_at if now is None else now
        return True

    def observe_json(self, payload: dict[str, Any], *, now: float) -> None:
        event_type = str(payload.get("type", ""))
        self.events.append(
            {
                "elapsed_ms": _elapsed_ms(self.started_at, now),
                "type": event_type,
                "payload": payload,
            }
        )
        if event_type == "transcript_final":
            self.mark("transcript_final", now)
            text = payload.get("text")
            if isinstance(text, str):
                self.transcript_text = text
        elif event_type == "reply_text":
            self.mark("first_reply_text", now)
            delta = payload.get("delta")
            if isinstance(delta, str):
                self.reply_text += delta
        elif event_type == "audio_start":
            self.mark("audio_start_event", now)
            turn_id = payload.get("turn_id")
            if isinstance(turn_id, str):
                self.audio_turn_id = turn_id
        elif event_type == "audio_end":
            self.mark("audio_end_event", now)
        elif event_type == "reply_done":
            self.mark("reply_done", now)

    def observe_binary_audio(self, chunk: bytes, *, now: float) -> None:
        self.mark("first_binary_audio", now)
        self.binary_audio_chunks += 1
        self.binary_audio_bytes += len(chunk)

    def metrics_ms(self) -> dict[str, float | None]:
        return build_metrics_ms(self.timestamps)

    def to_summary(self, *, request: dict[str, Any]) -> dict[str, Any]:
        timestamp_ms = {
            name: _elapsed_ms(self.started_at, value)
            for name, value in sorted(self.timestamps.items(), key=lambda item: item[1])
        }
        return {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "request": request,
            "ok": self.timestamps.get("first_binary_audio") is not None,
            "transcript_text": self.transcript_text,
            "reply_text": self.reply_text,
            "binary_audio_chunks": self.binary_audio_chunks,
            "binary_audio_bytes": self.binary_audio_bytes,
            "audio_turn_id": self.audio_turn_id,
            "timestamps_ms": timestamp_ms,
            "metrics_ms": self.metrics_ms(),
            "events": self.events,
        }


def build_audio_chunks(
    voice_samples: np.ndarray,
    *,
    silence_ms: int,
    sample_rate: int = SAMPLE_RATE,
    chunk_samples: int = CHUNK_SAMPLES,
) -> list[AudioChunk]:
    chunks: list[AudioChunk] = []
    for start in range(0, len(voice_samples), chunk_samples):
        chunk = voice_samples[start : start + chunk_samples]
        if chunk.size == 0:
            continue
        if chunk.size < chunk_samples:
            chunk = np.pad(chunk, (0, chunk_samples - chunk.size))
        chunks.append(AudioChunk(samples=np.asarray(chunk, dtype=np.float32), is_voice=True))

    silence_sample_count = math.ceil(sample_rate * silence_ms / 1000)
    silence_chunk_count = math.ceil(silence_sample_count / chunk_samples)
    for _ in range(silence_chunk_count):
        chunks.append(
            AudioChunk(
                samples=np.zeros(chunk_samples, dtype=np.float32),
                is_voice=False,
            )
        )
    return chunks


def build_metrics_ms(timestamps: dict[str, float]) -> dict[str, float | None]:
    pairs = {
        "voice_end_to_transcript_final": ("last_voice_chunk_sent", "transcript_final"),
        "voice_end_to_first_reply_text": ("last_voice_chunk_sent", "first_reply_text"),
        "voice_end_to_first_binary_audio": ("last_voice_chunk_sent", "first_binary_audio"),
        "transcript_to_first_reply_text": ("transcript_final", "first_reply_text"),
        "transcript_to_first_binary_audio": ("transcript_final", "first_binary_audio"),
        "audio_send_start_to_first_binary_audio": (
            "audio_send_started",
            "first_binary_audio",
        ),
        "silence_done_to_first_binary_audio": (
            "silence_send_completed",
            "first_binary_audio",
        ),
    }
    return {
        name: _diff_ms(timestamps, start, end)
        for name, (start, end) in pairs.items()
    }


def generate_say_wav(
    *,
    text: str,
    voice: str,
    rate: int,
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "say",
            "-v",
            voice,
            "-r",
            str(rate),
            "--data-format=LEI16@16000",
            "-o",
            str(output_path),
            text,
        ],
        check=True,
    )


def load_wav_float32(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    if sample_rate != SAMPLE_RATE:
        raise ValueError(f"expected {SAMPLE_RATE}Hz wav, got {sample_rate}Hz: {path}")
    if sample_width != 2:
        raise ValueError(f"expected 16-bit PCM wav, got sample_width={sample_width}: {path}")
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return np.asarray(samples, dtype=np.float32)


async def run_ws_voice_latency(
    *,
    url: str,
    chunks: list[AudioChunk],
    text: str,
    voice: str,
    realtime: bool,
    timeout_sec: float,
    output_path: Path | None,
    chunk_samples: int = CHUNK_SAMPLES,
    sample_rate: int = SAMPLE_RATE,
) -> dict[str, Any]:
    import websockets

    started_at = time.perf_counter()
    recorder = WsLatencyRecorder(started_at=started_at)
    request = {
        "url": url,
        "text": text,
        "voice": voice,
        "realtime": realtime,
        "timeout_sec": timeout_sec,
        "sample_rate": sample_rate,
        "chunk_samples": chunk_samples,
        "voice_chunks": sum(1 for chunk in chunks if chunk.is_voice),
        "silence_chunks": sum(1 for chunk in chunks if not chunk.is_voice),
    }

    async with websockets.connect(url, max_size=None) as websocket:
        recorder.mark("connected", time.perf_counter())
        done = asyncio.Event()

        async def receive_loop() -> None:
            try:
                async for message in websocket:
                    now = time.perf_counter()
                    if isinstance(message, bytes):
                        recorder.observe_binary_audio(message, now=now)
                        continue
                    try:
                        payload = json.loads(message)
                    except json.JSONDecodeError:
                        recorder.events.append(
                            {
                                "elapsed_ms": _elapsed_ms(started_at, now),
                                "type": "invalid_json",
                                "payload": {"raw": message},
                            }
                        )
                        continue
                    if isinstance(payload, dict):
                        recorder.observe_json(payload, now=now)
                        if payload.get("type") == "reply_done":
                            done.set()
            except websockets.exceptions.ConnectionClosed as exc:
                recorder.events.append(
                    {
                        "elapsed_ms": _elapsed_ms(started_at, time.perf_counter()),
                        "type": "connection_closed",
                        "payload": {"code": exc.code, "reason": exc.reason},
                    }
                )
                done.set()

        receive_task = asyncio.create_task(receive_loop())
        recorder.mark("audio_send_started", time.perf_counter())
        try:
            last_chunk_was_voice = False
            for chunk in chunks:
                if last_chunk_was_voice and not chunk.is_voice:
                    recorder.mark("last_voice_chunk_sent", time.perf_counter())
                await websocket.send(chunk.to_wire_bytes())
                last_chunk_was_voice = chunk.is_voice
                if realtime:
                    await asyncio.sleep(chunk.samples.size / sample_rate)
            if last_chunk_was_voice:
                recorder.mark("last_voice_chunk_sent", time.perf_counter())
            recorder.mark("silence_send_completed", time.perf_counter())
            try:
                await asyncio.wait_for(done.wait(), timeout=timeout_sec)
            except TimeoutError:
                recorder.mark("timeout", time.perf_counter())
        finally:
            with suppress(websockets.exceptions.ConnectionClosed):
                await websocket.send(json.dumps({"type": "client_stop"}))
            receive_task.cancel()
            with suppress(asyncio.CancelledError):
                await receive_task

    summary = recorder.to_summary(request=request)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return summary


def _default_output_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return ROOT / "logs" / f"ws-voice-latency-{stamp}.json"


def _diff_ms(timestamps: dict[str, float], start: str, end: str) -> float | None:
    if start not in timestamps or end not in timestamps:
        return None
    return _elapsed_ms(timestamps[start], timestamps[end])


def _elapsed_ms(started_at: float, now: float) -> float:
    return round((now - started_at) * 1000, 1)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Send macOS say audio plus trailing silence to Tomoko /ws and measure "
            "human-perceived first-audio latency through VAD/STT/LLM/TTS."
        )
    )
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws")
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--voice", default="Kyoko")
    parser.add_argument("--rate", type=int, default=180)
    parser.add_argument("--silence-ms", type=int, default=1200)
    parser.add_argument("--timeout-sec", type=float, default=90.0)
    parser.add_argument("--output", type=Path, default=_default_output_path())
    parser.add_argument(
        "--no-realtime",
        action="store_true",
        help="Send chunks as fast as possible instead of browser-like realtime pacing.",
    )
    parser.add_argument(
        "--keep-input-wav",
        type=Path,
        default=None,
        help="Optional path to keep the generated 16kHz mono WAV input.",
    )
    return parser.parse_args()


async def _amain() -> int:
    args = _parse_args()
    with tempfile.TemporaryDirectory(prefix="tomoko-ws-latency-") as temp_dir:
        wav_path = args.keep_input_wav or Path(temp_dir) / "input.wav"
        generate_say_wav(text=args.text, voice=args.voice, rate=args.rate, output_path=wav_path)
        samples = load_wav_float32(wav_path)
        chunks = build_audio_chunks(samples, silence_ms=args.silence_ms)
        summary = await run_ws_voice_latency(
            url=args.url,
            chunks=chunks,
            text=args.text,
            voice=args.voice,
            realtime=not args.no_realtime,
            timeout_sec=args.timeout_sec,
            output_path=args.output,
        )
    print(json.dumps(summary["metrics_ms"], ensure_ascii=False, indent=2))
    print(f"artifact: {args.output}")
    print(f"transcript: {summary.get('transcript_text')}")
    print(f"reply: {summary.get('reply_text')}")
    return 0 if summary["ok"] else 1


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
