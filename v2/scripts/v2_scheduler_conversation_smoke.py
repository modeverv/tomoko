from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from server.hot_path.model_executor import StaticWavTtsBackend
from server.hot_path.speech_executor import SpeechOrderExecutor
from server.llm.chat import StaticChatBackend
from server.shared.logging import JsonlLogger
from server.shared.models import PartialTranscriptObservation, utc_now
from server.tomoko.conversation import TomokoConversationCore
from server.tomoko.scheduler import SpeechScheduler
from server.tomoko.semantic import SemanticSaturationJudge
from server.tomoko.session import SessionBoundaryModel


@dataclass(slots=True)
class SchedulerConversationSmokeResult:
    transcript: str
    reply: str | None
    action: str
    speech_order_mode: str | None
    audio_chunks: int
    audio_bytes: int
    total_ms: float
    score: float
    score_breakdown: dict[str, float]
    artifact_path: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", default="トモコ、短く返事して")
    parser.add_argument("--reply", default="うん、聞こえてるよ。")
    parser.add_argument("--output-dir", default="logs")
    parser.add_argument("--append-latency-log", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


async def run_smoke(args: argparse.Namespace) -> SchedulerConversationSmokeResult:
    started = time.perf_counter()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    artifact = output_dir / f"scheduler-conversation-smoke-{stamp}.json"
    logger = JsonlLogger(Path("logs/v2-runtime.jsonl"))
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(logger=logger),
        scheduler=SpeechScheduler(logger=logger),
        chat_backend=StaticChatBackend([args.reply]),
    )
    executor = SpeechOrderExecutor(StaticWavTtsBackend([b"RIFFxxxxWAVEdata"]))
    now = utc_now()
    turn = await core.handle_observation(
        PartialTranscriptObservation(
            text=args.text,
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )
    audio = await executor.execute(turn.speech_order) if turn.speech_order is not None else None
    result = SchedulerConversationSmokeResult(
        transcript=args.text,
        reply=turn.speech_order.text if turn.speech_order is not None else None,
        action=turn.scheduler_output.action.value,
        speech_order_mode=turn.speech_order.mode.value if turn.speech_order is not None else None,
        audio_chunks=len(audio.audio_chunks) if audio is not None else 0,
        audio_bytes=(
            sum(len(chunk.chunk) for chunk in audio.audio_chunks) if audio is not None else 0
        ),
        total_ms=(time.perf_counter() - started) * 1000.0,
        score=turn.scheduler_output.score,
        score_breakdown=turn.scheduler_output.score_breakdown,
        artifact_path=str(artifact),
    )
    payload = asdict(result)
    artifact.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.log("scheduler_conversation_smoke", **payload)
    if args.append_latency_log:
        append_latency_log(result)
    return result


def append_latency_log(result: SchedulerConversationSmokeResult) -> None:
    path = Path("_docs/latency.md")
    line = (
        f"| 2026-06-18 | Tomoko v2 scheduler fake vertical slice | "
        f"`STT -> saturation -> scheduler -> LLM text -> speech-order -> TTS` | "
        f"total {result.total_ms:.1f}ms | action `{result.action}`, "
        f"mode `{result.speech_order_mode}`, audio chunks {result.audio_chunks}, "
        f"artifact `{result.artifact_path}`. |\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)


async def async_main() -> None:
    result = await run_smoke(parse_args())
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
