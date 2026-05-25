from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from _tools.bench_stt_backends import (  # noqa: E402
    DEFAULT_BACKENDS,
    DEFAULT_LOAD_CONVERSATION_TEXT,
    DEFAULT_LOAD_TTS_TEXT,
    DEFAULT_TEXT,
    ConcurrentLoadConfig,
    ConcurrentLoadRunner,
    _make_sample_audio,
    _make_segment,
    _measure_once,
    _read_wav_float32,
    _validate_load_config,
    parse_backend_names,
)
from server.edge.pipeline.stt import create_stt_transcriber  # noqa: E402
from server.shared.config import BackendSpec, NodeConfig  # noqa: E402


@dataclass(slots=True)
class RunningStats:
    count: int = 0
    avg_ms: float = 0.0
    min_ms: float | None = None
    max_ms: float | None = None

    def add(self, elapsed_ms: float) -> None:
        self.count += 1
        self.avg_ms += (elapsed_ms - self.avg_ms) / self.count
        self.min_ms = elapsed_ms if self.min_ms is None else min(self.min_ms, elapsed_ms)
        self.max_ms = elapsed_ms if self.max_ms is None else max(self.max_ms, elapsed_ms)


@dataclass(slots=True)
class SoakStats:
    backend: str
    type: str
    model: str | None
    command: str | None
    streaming: bool
    started_at: str
    stats: RunningStats
    recent_ms: list[float]
    last_text: str = ""
    errors: int = 0

    def add_run(self, elapsed_ms: float, text: str, *, recent_limit: int) -> None:
        self.stats.add(elapsed_ms)
        self.recent_ms.append(elapsed_ms)
        if len(self.recent_ms) > recent_limit:
            del self.recent_ms[: len(self.recent_ms) - recent_limit]
        self.last_text = text

    def recent_p95(self) -> float:
        return percentile(self.recent_ms, 95)


def percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((percent / 100) * (len(ordered) - 1)))))
    return ordered[index]


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run STT backends continuously until Ctrl-C for load/soak testing.",
    )
    parser.add_argument("--config", default="config/central_realtime.toml")
    parser.add_argument("--backends", default=DEFAULT_BACKENDS)
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--audio-file", default=None)
    parser.add_argument(
        "--output",
        default="logs/stt-soak.jsonl",
        help="JSONL path for per-run samples and final summaries.",
    )
    parser.add_argument(
        "--status-interval-sec",
        type=float,
        default=5.0,
        help="How often to print running stats.",
    )
    parser.add_argument(
        "--sleep-ms",
        type=int,
        default=0,
        help="Optional idle sleep after each full backend cycle.",
    )
    parser.add_argument(
        "--recent-window",
        type=int,
        default=200,
        help="Number of recent samples used for rolling p95.",
    )
    parser.add_argument("--load-tts-backend", default=None)
    parser.add_argument("--load-conversation-backend", default=None)
    parser.add_argument("--load-start-delay-ms", type=int, default=20)
    parser.add_argument("--load-tts-text", default=DEFAULT_LOAD_TTS_TEXT)
    parser.add_argument("--load-conversation-text", default=DEFAULT_LOAD_CONVERSATION_TEXT)
    args = parser.parse_args()

    await soak_from_args(args)


async def soak_from_args(args: argparse.Namespace) -> list[SoakStats]:
    if args.status_interval_sec <= 0:
        raise ValueError("--status-interval-sec must be > 0")
    if args.sleep_ms < 0:
        raise ValueError("--sleep-ms must be >= 0")
    if args.recent_window < 1:
        raise ValueError("--recent-window must be >= 1")

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
    segment = _make_segment(_read_wav_float32(audio_path))
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    transcribers: list[tuple[BackendSpec, Any]] = []
    try:
        for backend_name in backend_names:
            spec = config.backends.get(backend_name)
            if spec is None:
                raise KeyError(f"unknown backend in {config_path}: {backend_name}")
            transcriber = create_stt_transcriber(spec)
            await _measure_once(transcriber, segment)
            transcribers.append((spec, transcriber))

        stats = [
            SoakStats(
                backend=spec.name,
                type=spec.type,
                model=spec.model_path or spec.model,
                command=spec.command,
                streaming=spec.streaming,
                started_at=datetime.now(UTC).isoformat(),
                stats=RunningStats(),
                recent_ms=[],
            )
            for spec, _transcriber in transcribers
        ]

        stop_event = asyncio.Event()
        _install_signal_handlers(stop_event)
        next_status = time.monotonic() + args.status_interval_sec
        started = time.monotonic()

        with output_path.open("a", encoding="utf-8") as fp:
            _write_jsonl(
                fp,
                {
                    "type": "start",
                    "generated_at": datetime.now(UTC).isoformat(),
                    "config": str(config_path),
                    "audio_path": str(audio_path),
                    "sample_text": args.text,
                    "backends": backend_names,
                    "concurrent_load": asdict(load_config),
                },
            )
            while not stop_event.is_set():
                for index, (_spec, transcriber) in enumerate(transcribers):
                    if stop_event.is_set():
                        break
                    run_started = datetime.now(UTC).isoformat()
                    try:
                        measurement = await _measure_once(transcriber, segment, load_runner)
                        stats[index].add_run(
                            measurement.elapsed_ms,
                            measurement.text,
                            recent_limit=args.recent_window,
                        )
                        _write_jsonl(
                            fp,
                            {
                                "type": "sample",
                                "measured_at": run_started,
                                "backend": stats[index].backend,
                                **asdict(measurement),
                            },
                        )
                    except Exception as e:
                        stats[index].errors += 1
                        _write_jsonl(
                            fp,
                            {
                                "type": "error",
                                "measured_at": run_started,
                                "backend": stats[index].backend,
                                "error": str(e),
                            },
                        )
                        print(f"ERROR backend={stats[index].backend} error={e}", flush=True)

                now = time.monotonic()
                if now >= next_status:
                    print_status(stats, elapsed_sec=now - started, load_label=load_config.label)
                    next_status = now + args.status_interval_sec
                if args.sleep_ms > 0:
                    await asyncio.sleep(args.sleep_ms / 1000)

            elapsed_sec = time.monotonic() - started
            summaries = [summary_payload(item, elapsed_sec=elapsed_sec) for item in stats]
            for payload in summaries:
                _write_jsonl(fp, {"type": "summary", **payload})
        print_status(stats, elapsed_sec=time.monotonic() - started, load_label=load_config.label)
        print(f"JSONL: {output_path}")
        return stats
    finally:
        for _spec, transcriber in transcribers:
            close = getattr(transcriber, "close", None)
            if close is not None:
                await close()


def print_status(stats: list[SoakStats], *, elapsed_sec: float, load_label: str) -> None:
    print(f"\n# elapsed={elapsed_sec:.1f}s load={load_label}", flush=True)
    print("| backend | n | avg_ms | min_ms | max_ms | recent_p95_ms | qps | errors | text |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for item in stats:
        qps = item.stats.count / elapsed_sec if elapsed_sec > 0 else 0.0
        text = item.last_text.replace("|", "\\|")
        print(
            f"| {item.backend} | {item.stats.count} | {item.stats.avg_ms:.1f} | "
            f"{(item.stats.min_ms or 0.0):.1f} | {(item.stats.max_ms or 0.0):.1f} | "
            f"{item.recent_p95():.1f} | {qps:.2f} | {item.errors} | {text} |",
            flush=True,
        )


def summary_payload(item: SoakStats, *, elapsed_sec: float) -> dict[str, object]:
    return {
        "backend": item.backend,
        "type": item.type,
        "model": item.model,
        "command": item.command,
        "streaming": item.streaming,
        "started_at": item.started_at,
        "elapsed_sec": elapsed_sec,
        "count": item.stats.count,
        "avg_ms": item.stats.avg_ms,
        "min_ms": item.stats.min_ms,
        "max_ms": item.stats.max_ms,
        "recent_p95_ms": item.recent_p95(),
        "qps": item.stats.count / elapsed_sec if elapsed_sec > 0 else 0.0,
        "errors": item.errors,
        "last_text": item.last_text,
    }


def _write_jsonl(fp: Any, payload: dict[str, object]) -> None:
    fp.write(json.dumps(payload, ensure_ascii=False) + "\n")
    fp.flush()


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, stop_event.set)
        loop.add_signal_handler(signal.SIGTERM, stop_event.set)
    except NotImplementedError:
        pass


if __name__ == "__main__":
    asyncio.run(main())
