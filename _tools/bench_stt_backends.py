from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time
import wave
from collections.abc import Coroutine
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.edge.pipeline.stt import create_stt_transcriber  # noqa: E402
from server.shared.config import BackendSpec, NodeConfig  # noqa: E402
from server.shared.inference.router import InferenceRouter  # noqa: E402
from server.shared.inference.tts import create_tts_backend  # noqa: E402
from server.shared.models import SpeechSegment, TTSInput  # noqa: E402

DEFAULT_BACKENDS = "local_whisper_mlx_small,local_whisperkit_serve_small"
DEFAULT_TEXT = "ともこ、さんたすさんは、いくつですか。"
DEFAULT_LOAD_TTS_TEXT = "うん、わかった。少し待ってね。"
DEFAULT_LOAD_CONVERSATION_TEXT = "トモコ、短く一言で返事して。"


@dataclass(frozen=True, slots=True)
class Measurement:
    elapsed_ms: float
    text: str
    load_label: str | None = None
    load_elapsed_ms: float | None = None


@dataclass(frozen=True, slots=True)
class MeasurementSummary:
    avg_ms: float
    min_ms: float
    max_ms: float


@dataclass(frozen=True, slots=True)
class BackendBenchResult:
    backend: str
    type: str
    model: str | None
    command: str | None
    streaming: bool
    warmup_ms: float
    warmup_text: str
    avg_ms: float
    min_ms: float
    max_ms: float
    runs: list[Measurement]


@dataclass(frozen=True, slots=True)
class ConcurrentLoadConfig:
    tts_backend: str | None
    conversation_backend: str | None
    start_delay_ms: int
    tts_text: str
    conversation_text: str

    @property
    def label(self) -> str:
        labels: list[str] = []
        if self.tts_backend:
            labels.append(f"tts:{self.tts_backend}")
        if self.conversation_backend:
            labels.append(f"conversation:{self.conversation_backend}")
        return "+".join(labels) if labels else "idle"


class ConcurrentLoadRunner:
    def __init__(self, config: NodeConfig, load_config: ConcurrentLoadConfig) -> None:
        self.config = config
        self.load_config = load_config
        self._tts_backend: Any | None = None
        self._conversation_backend: Any | None = None

        if load_config.tts_backend:
            spec = config.backends[load_config.tts_backend]
            self._tts_backend = create_tts_backend(spec)
        if load_config.conversation_backend:
            router = InferenceRouter(config)
            self._conversation_backend = router.backends[load_config.conversation_backend]

    async def warm_up(self) -> None:
        tasks: list[Coroutine[Any, Any, None]] = []
        if self._tts_backend is not None:
            tasks.append(self._tts_backend.warm_up())
        if self._conversation_backend is not None:
            tasks.append(self._run_conversation_load())
        if tasks:
            await asyncio.gather(*tasks)

    async def run_once(self) -> float | None:
        tasks: list[asyncio.Task[None]] = []
        if self._tts_backend is not None:
            tasks.append(asyncio.create_task(self._run_tts_load()))
        if self._conversation_backend is not None:
            tasks.append(asyncio.create_task(self._run_conversation_load()))
        if not tasks:
            return None

        start = time.perf_counter()
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
        return (time.perf_counter() - start) * 1000

    async def _run_tts_load(self) -> None:
        assert self._tts_backend is not None
        async for _chunk in self._tts_backend.synthesize(
            TTSInput(text=self.load_config.tts_text, style="neutral")
        ):
            pass

    async def _run_conversation_load(self) -> None:
        assert self._conversation_backend is not None
        async for _delta in self._conversation_backend.chat_stream(
            "あなたは短く日本語で答えるアシスタントです。",
            [{"role": "user", "content": self.load_config.conversation_text}],
        ):
            pass


def parse_backend_names(value: str) -> list[str]:
    return [name.strip() for name in value.split(",") if name.strip()]


def summarize_measurements(measurements: list[Measurement]) -> MeasurementSummary:
    if not measurements:
        raise ValueError("at least one measurement is required")
    values = [measurement.elapsed_ms for measurement in measurements]
    return MeasurementSummary(
        avg_ms=sum(values) / len(values),
        min_ms=min(values),
        max_ms=max(values),
    )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark configured STT backends such as MLX Whisper "
            "and WhisperKit CoreML serve."
        ),
    )
    parser.add_argument("--config", default="config/central_realtime.toml")
    parser.add_argument(
        "--backends",
        default=DEFAULT_BACKENDS,
        help="Comma-separated backend names from config/*.toml.",
    )
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument(
        "--audio-file",
        default=None,
        help="Optional mono/stereo WAV input. When omitted, macOS say generates a 16kHz sample.",
    )
    parser.add_argument(
        "--output",
        default="logs/stt-mlx-coreml-bench.json",
        help="JSON summary path.",
    )
    parser.add_argument(
        "--load-tts-backend",
        default=None,
        help="Optional TTS backend name to run concurrently with each measured STT call.",
    )
    parser.add_argument(
        "--load-conversation-backend",
        default=None,
        help="Optional conversation backend name to run concurrently with each measured STT call.",
    )
    parser.add_argument(
        "--load-start-delay-ms",
        type=int,
        default=20,
        help="Delay between starting concurrent load and measuring STT.",
    )
    parser.add_argument("--load-tts-text", default=DEFAULT_LOAD_TTS_TEXT)
    parser.add_argument("--load-conversation-text", default=DEFAULT_LOAD_CONVERSATION_TEXT)
    args = parser.parse_args()

    results = await bench_from_args(args)
    print_results(results)
    print(f"\nJSON: {args.output}")


async def bench_from_args(args: argparse.Namespace) -> list[BackendBenchResult]:
    if args.runs < 1:
        raise ValueError("--runs must be >= 1")

    config_path = Path(args.config)
    config = NodeConfig.load(config_path)
    backend_names = parse_backend_names(args.backends)
    if not backend_names:
        raise ValueError("--backends must include at least one backend name")
    load_config = ConcurrentLoadConfig(
        tts_backend=args.load_tts_backend,
        conversation_backend=args.load_conversation_backend,
        start_delay_ms=args.load_start_delay_ms,
        tts_text=args.load_tts_text,
        conversation_text=args.load_conversation_text,
    )
    _validate_load_config(config, load_config)
    load_runner = ConcurrentLoadRunner(config, load_config)
    await load_runner.warm_up()

    audio_path = Path(args.audio_file) if args.audio_file else _make_sample_audio(args.text)
    audio = _read_wav_float32(audio_path)
    segment = _make_segment(audio)

    results: list[BackendBenchResult] = []
    for backend_name in backend_names:
        spec = config.backends.get(backend_name)
        if spec is None:
            raise KeyError(f"unknown backend in {config_path}: {backend_name}")
        result = await bench_backend(spec, segment, args.runs, load_runner)
        results.append(result)

    write_json_summary(
        Path(args.output),
        config_path=config_path,
        audio_path=audio_path,
        sample_text=args.text,
        load_config=load_config,
        results=results,
    )
    return results


async def bench_backend(
    spec: BackendSpec,
    segment: SpeechSegment,
    runs: int,
    load_runner: ConcurrentLoadRunner | None = None,
) -> BackendBenchResult:
    transcriber = create_stt_transcriber(spec)
    try:
        warmup = await _measure_once(transcriber, segment)
        measurements: list[Measurement] = []
        for _ in range(runs):
            measurements.append(await _measure_once(transcriber, segment, load_runner))
    finally:
        close = getattr(transcriber, "close", None)
        if close is not None:
            await close()

    summary = summarize_measurements(measurements)
    return BackendBenchResult(
        backend=spec.name,
        type=spec.type,
        model=spec.model_path or spec.model,
        command=spec.command,
        streaming=spec.streaming,
        warmup_ms=warmup.elapsed_ms,
        warmup_text=warmup.text,
        avg_ms=summary.avg_ms,
        min_ms=summary.min_ms,
        max_ms=summary.max_ms,
        runs=measurements,
    )


async def _measure_once(
    transcriber: Any,
    segment: SpeechSegment,
    load_runner: ConcurrentLoadRunner | None = None,
) -> Measurement:
    load_task: asyncio.Task[float | None] | None = None
    if load_runner is not None and load_runner.load_config.label != "idle":
        load_task = asyncio.create_task(load_runner.run_once())
        await asyncio.sleep(load_runner.load_config.start_delay_ms / 1000)

    start = time.perf_counter()
    transcript = await transcriber.transcribe(segment)
    elapsed_ms = (time.perf_counter() - start) * 1000
    load_elapsed_ms = await load_task if load_task is not None else None
    return Measurement(
        elapsed_ms=elapsed_ms,
        text=transcript.text,
        load_label=load_runner.load_config.label if load_runner is not None else "idle",
        load_elapsed_ms=load_elapsed_ms,
    )


def print_results(results: list[BackendBenchResult]) -> None:
    print(
        "| backend | type | model | streaming | load | warmup_ms | avg_ms | "
        "min_ms | max_ms | load_avg_ms | text |"
    )
    print("|---|---|---|---:|---|---:|---:|---:|---:|---:|---|")
    for result in results:
        text = result.runs[-1].text.replace("|", "\\|") if result.runs else ""
        load_label = result.runs[-1].load_label if result.runs else "idle"
        load_values = [
            run.load_elapsed_ms for run in result.runs if run.load_elapsed_ms is not None
        ]
        load_avg_ms = sum(load_values) / len(load_values) if load_values else 0.0
        print(
            f"| {result.backend} | {result.type} | {result.model or ''} | "
            f"{str(result.streaming).lower()} | {load_label} | {result.warmup_ms:.1f} | "
            f"{result.avg_ms:.1f} | {result.min_ms:.1f} | {result.max_ms:.1f} | "
            f"{load_avg_ms:.1f} | {text} |"
        )


def write_json_summary(
    path: Path,
    *,
    config_path: Path,
    audio_path: Path,
    sample_text: str,
    load_config: ConcurrentLoadConfig | None = None,
    results: list[BackendBenchResult],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "config": str(config_path),
        "audio_path": str(audio_path),
        "sample_text": sample_text,
        "concurrent_load": asdict(load_config) if load_config is not None else None,
        "results": [asdict(result) for result in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _validate_load_config(config: NodeConfig, load_config: ConcurrentLoadConfig) -> None:
    if load_config.tts_backend is not None:
        spec = config.backends.get(load_config.tts_backend)
        if spec is None:
            raise KeyError(f"unknown TTS load backend: {load_config.tts_backend}")
        supported_tts = {
            "say",
            "kokoro_mlx",
            "kokoro_coreml",
            "irodori_mlx",
            "irodori_mlx_stream",
            "qwen3_mlx",
        }
        if spec.type not in supported_tts:
            raise ValueError(f"backend is not a TTS backend: {load_config.tts_backend}")
    if load_config.conversation_backend is not None:
        spec = config.backends.get(load_config.conversation_backend)
        if spec is None:
            raise KeyError(
                f"unknown conversation load backend: {load_config.conversation_backend}"
            )
        if spec.type not in {"ollama", "gemma_mlx", "lm_studio", "mlx_lm"}:
            raise ValueError(
                f"backend is not a conversation backend: {load_config.conversation_backend}"
            )
    if load_config.start_delay_ms < 0:
        raise ValueError("--load-start-delay-ms must be >= 0")


def _make_sample_audio(text: str) -> Path:
    if shutil.which("say") is None:
        raise RuntimeError("macOS say command is required when --audio-file is omitted")

    output_dir = ROOT / "logs" / "stt-bench"
    output_dir.mkdir(parents=True, exist_ok=True)
    wav_path = output_dir / "sample.wav"
    subprocess.run(
        [
            "say",
            "-v",
            "Kyoko",
            "--data-format=LEI16@16000",
            "-o",
            str(wav_path),
            text,
        ],
        check=True,
    )
    return wav_path


def _read_wav_float32(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wav:
        frames = wav.readframes(wav.getnframes())
        if wav.getsampwidth() != 2:
            raise ValueError(f"expected 16-bit WAV: {path}")
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        if wav.getnchannels() != 1:
            audio = audio.reshape(-1, wav.getnchannels()).mean(axis=1)
    return audio.astype(np.float32, copy=False)


def _make_segment(audio: np.ndarray) -> SpeechSegment:
    now = datetime.now(UTC)
    return SpeechSegment(
        audio=audio,
        started_at=now,
        ended_at=now,
        device_id="bench",
        vad_confidence=1.0,
    )


if __name__ == "__main__":
    asyncio.run(main())
