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
    DEFAULT_TEXT,
    ConcurrentLoadConfig,
    _make_sample_audio,
    _make_segment,
    _measure_once,
    _read_wav_float32,
    _validate_load_config,
)
from _tools.soak_stt_backends import RunningStats, percentile  # noqa: E402
from server.edge.pipeline.stt import create_stt_transcriber  # noqa: E402
from server.shared.config import BackendSpec, NodeConfig  # noqa: E402
from server.shared.inference.router import InferenceRouter  # noqa: E402
from server.shared.inference.tts import create_tts_backend  # noqa: E402
from server.shared.models import TTSInput  # noqa: E402

DEFAULT_MLX_STT_BACKEND = "local_whisper_mlx_small"
DEFAULT_COREML_STT_BACKEND = "local_whisperkit_serve_small"
DEFAULT_COREML_TTS_BACKEND = "supertonic_coreml_f1"
DEFAULT_MLX_CONVERSATION_BACKEND = "local_lfm25_12b_jp_mlx"
DEFAULT_STRESS_TTS_TEXT = (
    "うん、わかった。少し待ってね。今日は処理負荷を見るために、"
    "いつもより少し長めに、自然な長さの返事を続けて読み上げます。"
)
DEFAULT_STRESS_CONVERSATION_TEXT = (
    "ベンチマーク用です。ローカル推論の負荷を測るため、"
    "今日の作業状況、次に見るべき観点、判断の保留点について、"
    "日本語で八文程度の自然な返事を書いてください。"
)


@dataclass(frozen=True, slots=True)
class VoiceStackScenario:
    name: str
    stt_backend: str
    tts_backend: str
    conversation_backend: str

    @property
    def load_key(self) -> tuple[str, str]:
        return (self.tts_backend, self.conversation_backend)


@dataclass(frozen=True, slots=True)
class StackLoadConfig:
    tts_backend: str
    conversation_backend: str
    start_delay_ms: int
    tts_text: str
    conversation_text: str
    tts_repeats: int
    conversation_repeats: int
    tts_workers: int
    conversation_workers: int

    @property
    def label(self) -> str:
        return (
            f"tts:{self.tts_backend}*{self.tts_repeats}w{self.tts_workers}"
            f"+conversation:{self.conversation_backend}"
            f"*{self.conversation_repeats}w{self.conversation_workers}"
        )


class StackLoadRunner:
    def __init__(self, config: NodeConfig, load_config: StackLoadConfig) -> None:
        self.load_config = load_config
        self._tts_backend = create_tts_backend(config.backends[load_config.tts_backend])
        self._conversation_backend = InferenceRouter(config).backends[
            load_config.conversation_backend
        ]

    async def warm_up(self) -> None:
        await asyncio.gather(self._run_tts_once(), self._run_conversation_once())

    async def run_once(self) -> float:
        tasks: list[asyncio.Task[None]] = []
        for _ in range(self.load_config.tts_workers):
            tasks.append(asyncio.create_task(self._run_tts_loop()))
        for _ in range(self.load_config.conversation_workers):
            tasks.append(asyncio.create_task(self._run_conversation_loop()))

        start = time.perf_counter()
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
        return (time.perf_counter() - start) * 1000

    async def _run_tts_loop(self) -> None:
        for _ in range(self.load_config.tts_repeats):
            await self._run_tts_once()

    async def _run_tts_once(self) -> None:
        async for _chunk in self._tts_backend.synthesize(
            TTSInput(text=self.load_config.tts_text, style="neutral")
        ):
            pass

    async def _run_conversation_loop(self) -> None:
        for _ in range(self.load_config.conversation_repeats):
            await self._run_conversation_once()

    async def _run_conversation_once(self) -> None:
        async for _delta in self._conversation_backend.chat_stream(
            "あなたは日本語で自然に答えるアシスタントです。短すぎる返答にしないでください。",
            [{"role": "user", "content": self.load_config.conversation_text}],
        ):
            pass


@dataclass(slots=True)
class ScenarioStats:
    scenario: VoiceStackScenario
    stt_spec: BackendSpec
    started_at: str
    stt_stats: RunningStats
    load_stats: RunningStats
    recent_stt_ms: list[float]
    recent_load_ms: list[float]
    last_text: str = ""
    errors: int = 0

    def add_run(
        self,
        stt_elapsed_ms: float,
        load_elapsed_ms: float | None,
        text: str,
        *,
        recent_limit: int,
    ) -> None:
        self.stt_stats.add(stt_elapsed_ms)
        self.recent_stt_ms.append(stt_elapsed_ms)
        if len(self.recent_stt_ms) > recent_limit:
            del self.recent_stt_ms[: len(self.recent_stt_ms) - recent_limit]

        if load_elapsed_ms is not None:
            self.load_stats.add(load_elapsed_ms)
            self.recent_load_ms.append(load_elapsed_ms)
            if len(self.recent_load_ms) > recent_limit:
                del self.recent_load_ms[: len(self.recent_load_ms) - recent_limit]

        self.last_text = text

    def stt_recent_p95(self) -> float:
        return percentile(self.recent_stt_ms, 95)

    def load_recent_p95(self) -> float:
        return percentile(self.recent_load_ms, 95)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compare MLX STT and CoreML STT while CoreML TTS and MLX LLM load run."
        ),
    )
    parser.add_argument("--config", default="config/central_realtime.toml")
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--audio-file", default=None)
    parser.add_argument(
        "--output",
        default="logs/voice-stack-soak.jsonl",
        help="JSONL path for per-run samples and final summaries.",
    )
    parser.add_argument("--mlx-stt-backend", default=DEFAULT_MLX_STT_BACKEND)
    parser.add_argument("--coreml-stt-backend", default=DEFAULT_COREML_STT_BACKEND)
    parser.add_argument(
        "--tts-backend",
        default=DEFAULT_COREML_TTS_BACKEND,
        help="CoreML TTS load backend. Default is Supertonic CoreML F1.",
    )
    parser.add_argument("--conversation-backend", default=DEFAULT_MLX_CONVERSATION_BACKEND)
    parser.add_argument("--load-start-delay-ms", type=int, default=20)
    parser.add_argument("--load-tts-text", default=DEFAULT_STRESS_TTS_TEXT)
    parser.add_argument("--load-conversation-text", default=DEFAULT_STRESS_CONVERSATION_TEXT)
    parser.add_argument(
        "--load-tts-repeats",
        type=int,
        default=2,
        help="Sequential TTS generations per measured STT call.",
    )
    parser.add_argument(
        "--load-conversation-repeats",
        type=int,
        default=6,
        help="Sequential conversation generations per measured STT call.",
    )
    parser.add_argument(
        "--load-tts-workers",
        type=int,
        default=1,
        help="Parallel TTS load workers. Increase carefully; backend safety varies.",
    )
    parser.add_argument(
        "--load-conversation-workers",
        type=int,
        default=1,
        help="Parallel conversation load workers. Increase carefully; backend safety varies.",
    )
    parser.add_argument("--status-interval-sec", type=float, default=5.0)
    parser.add_argument("--sleep-ms", type=int, default=0)
    parser.add_argument("--recent-window", type=int, default=200)
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=0,
        help="Optional finite cycles for smoke tests. 0 means run until Ctrl-C.",
    )
    args = parser.parse_args()

    await soak_from_args(args)


async def soak_from_args(args: argparse.Namespace) -> list[ScenarioStats]:
    if args.status_interval_sec <= 0:
        raise ValueError("--status-interval-sec must be > 0")
    if args.sleep_ms < 0:
        raise ValueError("--sleep-ms must be >= 0")
    if args.recent_window < 1:
        raise ValueError("--recent-window must be >= 1")
    if args.max_cycles < 0:
        raise ValueError("--max-cycles must be >= 0")
    if args.load_tts_repeats < 1:
        raise ValueError("--load-tts-repeats must be >= 1")
    if args.load_conversation_repeats < 1:
        raise ValueError("--load-conversation-repeats must be >= 1")
    if args.load_tts_workers < 1:
        raise ValueError("--load-tts-workers must be >= 1")
    if args.load_conversation_workers < 1:
        raise ValueError("--load-conversation-workers must be >= 1")

    config_path = Path(args.config)
    config = NodeConfig.load(config_path)
    scenarios = build_default_scenarios(
        mlx_stt_backend=args.mlx_stt_backend,
        coreml_stt_backend=args.coreml_stt_backend,
        tts_backend=args.tts_backend,
        conversation_backend=args.conversation_backend,
    )
    load_runners = await _create_load_runners(config, scenarios, args)
    for load_runner in load_runners.values():
        await load_runner.warm_up()

    audio_path = Path(args.audio_file) if args.audio_file else _make_sample_audio(args.text)
    segment = _make_segment(_read_wav_float32(audio_path))
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    transcribers: list[tuple[VoiceStackScenario, BackendSpec, Any]] = []
    try:
        for scenario in scenarios:
            spec = _require_backend(config, scenario.stt_backend)
            transcriber = create_stt_transcriber(spec)
            await _measure_once(transcriber, segment)
            transcribers.append((scenario, spec, transcriber))

        stats = [
            ScenarioStats(
                scenario=scenario,
                stt_spec=spec,
                started_at=datetime.now(UTC).isoformat(),
                stt_stats=RunningStats(),
                load_stats=RunningStats(),
                recent_stt_ms=[],
                recent_load_ms=[],
            )
            for scenario, spec, _transcriber in transcribers
        ]

        stop_event = asyncio.Event()
        _install_signal_handlers(stop_event)
        started = time.monotonic()
        next_status = started + args.status_interval_sec
        cycles = 0

        with output_path.open("a", encoding="utf-8") as fp:
            _write_jsonl(
                fp,
                {
                    "type": "start",
                    "generated_at": datetime.now(UTC).isoformat(),
                    "config": str(config_path),
                    "audio_path": str(audio_path),
                    "sample_text": args.text,
                    "scenarios": [asdict(scenario) for scenario in scenarios],
                    "load": {
                        "start_delay_ms": args.load_start_delay_ms,
                        "tts_text": args.load_tts_text,
                        "conversation_text": args.load_conversation_text,
                        "tts_repeats": args.load_tts_repeats,
                        "conversation_repeats": args.load_conversation_repeats,
                        "tts_workers": args.load_tts_workers,
                        "conversation_workers": args.load_conversation_workers,
                    },
                },
            )
            while not stop_event.is_set():
                cycles += 1
                for index, (scenario, _spec, transcriber) in enumerate(transcribers):
                    if stop_event.is_set():
                        break
                    measured_at = datetime.now(UTC).isoformat()
                    try:
                        load_runner = load_runners[scenario.load_key]
                        measurement = await _measure_once(transcriber, segment, load_runner)
                        stats[index].add_run(
                            measurement.elapsed_ms,
                            measurement.load_elapsed_ms,
                            measurement.text,
                            recent_limit=args.recent_window,
                        )
                        _write_jsonl(
                            fp,
                            {
                                "type": "sample",
                                "measured_at": measured_at,
                                "scenario": scenario.name,
                                "stt_backend": scenario.stt_backend,
                                "tts_backend": scenario.tts_backend,
                                "conversation_backend": scenario.conversation_backend,
                                **asdict(measurement),
                            },
                        )
                    except Exception as e:
                        stats[index].errors += 1
                        _write_jsonl(
                            fp,
                            {
                                "type": "error",
                                "measured_at": measured_at,
                                "scenario": scenario.name,
                                "stt_backend": scenario.stt_backend,
                                "error": str(e),
                            },
                        )
                        print(f"ERROR scenario={scenario.name} error={e}", flush=True)

                now = time.monotonic()
                if now >= next_status:
                    print_status(stats, elapsed_sec=now - started)
                    next_status = now + args.status_interval_sec
                if args.max_cycles and cycles >= args.max_cycles:
                    stop_event.set()
                if args.sleep_ms > 0:
                    await asyncio.sleep(args.sleep_ms / 1000)

            elapsed_sec = time.monotonic() - started
            for item in stats:
                _write_jsonl(fp, {"type": "summary", **summary_payload(item, elapsed_sec)})

        print_status(stats, elapsed_sec=time.monotonic() - started)
        print(f"JSONL: {output_path}")
        return stats
    finally:
        for _scenario, _spec, transcriber in transcribers:
            close = getattr(transcriber, "close", None)
            if close is not None:
                await close()


def build_default_scenarios(
    *,
    mlx_stt_backend: str,
    coreml_stt_backend: str,
    tts_backend: str,
    conversation_backend: str,
) -> list[VoiceStackScenario]:
    return [
        VoiceStackScenario(
            name="mlx_stt_stack",
            stt_backend=mlx_stt_backend,
            tts_backend=tts_backend,
            conversation_backend=conversation_backend,
        ),
        VoiceStackScenario(
            name="coreml_stt_stack",
            stt_backend=coreml_stt_backend,
            tts_backend=tts_backend,
            conversation_backend=conversation_backend,
        ),
    ]


def print_status(stats: list[ScenarioStats], *, elapsed_sec: float) -> None:
    print(f"\n# elapsed={elapsed_sec:.1f}s", flush=True)
    print(
        "| scenario | stt | n | stt_avg_ms | stt_p95_ms | stt_max_ms | "
        "load_avg_ms | load_p95_ms | qps | errors | text |"
    )
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for item in stats:
        qps = item.stt_stats.count / elapsed_sec if elapsed_sec > 0 else 0.0
        text = item.last_text.replace("|", "\\|")
        print(
            f"| {item.scenario.name} | {item.scenario.stt_backend} | {item.stt_stats.count} | "
            f"{item.stt_stats.avg_ms:.1f} | {item.stt_recent_p95():.1f} | "
            f"{(item.stt_stats.max_ms or 0.0):.1f} | {item.load_stats.avg_ms:.1f} | "
            f"{item.load_recent_p95():.1f} | {qps:.2f} | {item.errors} | {text} |",
            flush=True,
        )


def summary_payload(item: ScenarioStats, elapsed_sec: float) -> dict[str, object]:
    return {
        "scenario": asdict(item.scenario),
        "stt_type": item.stt_spec.type,
        "stt_model": item.stt_spec.model_path or item.stt_spec.model,
        "stt_command": item.stt_spec.command,
        "started_at": item.started_at,
        "elapsed_sec": elapsed_sec,
        "count": item.stt_stats.count,
        "stt_avg_ms": item.stt_stats.avg_ms,
        "stt_min_ms": item.stt_stats.min_ms,
        "stt_max_ms": item.stt_stats.max_ms,
        "stt_recent_p95_ms": item.stt_recent_p95(),
        "load_avg_ms": item.load_stats.avg_ms,
        "load_min_ms": item.load_stats.min_ms,
        "load_max_ms": item.load_stats.max_ms,
        "load_recent_p95_ms": item.load_recent_p95(),
        "qps": item.stt_stats.count / elapsed_sec if elapsed_sec > 0 else 0.0,
        "errors": item.errors,
        "last_text": item.last_text,
    }


async def _create_load_runners(
    config: NodeConfig,
    scenarios: list[VoiceStackScenario],
    args: argparse.Namespace,
) -> dict[tuple[str, str], StackLoadRunner]:
    runners: dict[tuple[str, str], StackLoadRunner] = {}
    for scenario in scenarios:
        if scenario.load_key in runners:
            continue
        validation_config = ConcurrentLoadConfig(
            tts_backend=scenario.tts_backend,
            conversation_backend=scenario.conversation_backend,
            start_delay_ms=args.load_start_delay_ms,
            tts_text=args.load_tts_text,
            conversation_text=args.load_conversation_text,
        )
        _validate_load_config(config, validation_config)
        load_config = StackLoadConfig(
            tts_backend=scenario.tts_backend,
            conversation_backend=scenario.conversation_backend,
            start_delay_ms=args.load_start_delay_ms,
            tts_text=args.load_tts_text,
            conversation_text=args.load_conversation_text,
            tts_repeats=args.load_tts_repeats,
            conversation_repeats=args.load_conversation_repeats,
            tts_workers=args.load_tts_workers,
            conversation_workers=args.load_conversation_workers,
        )
        runners[scenario.load_key] = StackLoadRunner(config, load_config)
    return runners


def _require_backend(config: NodeConfig, backend_name: str) -> BackendSpec:
    spec = config.backends.get(backend_name)
    if spec is None:
        raise KeyError(f"unknown backend in config: {backend_name}")
    return spec


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
