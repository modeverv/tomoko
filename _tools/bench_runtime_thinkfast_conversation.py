#!/usr/bin/env python3
# ruff: noqa: E402
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.gateway.context import ContextSnapshotBuilder
from server.gateway.thinking.fast import ThinkFastMode
from server.shared.calendar import PostgresCalendarEventStore
from server.shared.config import NodeConfig
from server.shared.db import (
    PostgresConversationLogWriter,
    PostgresConversationSessionStore,
)
from server.shared.inference.embedding import create_embedding_backend
from server.shared.inference.router import InferenceRouter
from server.shared.memory import (
    PostgresConversationMemoryStore,
    PostgresConversationSessionSummaryStore,
)
from server.shared.models import ContextBuildPolicy, ThinkingInput, Transcript
from server.shared.persona import PostgresPersonaSnapshotStore
from server.shared.research_results import PostgresResearchResultStore
from server.shared.task_ledger import PostgresTaskLedgerStore

SAMPLE_TURNS = [
    "トモコ、dflashに移行するか迷ってる。26Bと31Bの使い分けを一緒に考えたい。",
    "短い返答は26B、記憶の整理や人格更新は31Bにする案はどう思う？",
    "実運用では会話ログが積み上がるから、プロンプトキャッシュが効く構造にしたい。",
    "日時やタスクの情報を毎回systemに入れるより、今の発話側に寄せると速くなる気がする。",
    "でもそれで人格や文脈が壊れるなら困る。速度より意味の安定を優先したい。",
    "ここまでの話を踏まえて、Tomoko本番に入れる判断としてどう進めるべき？",
]


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    config = NodeConfig.load(args.config)
    log_writer = PostgresConversationLogWriter(config.database.dsn)
    session_store = PostgresConversationSessionStore(config.database.dsn)
    embedding_backend = None
    if config.inference.embedding_backend:
        embedding_backend = create_embedding_backend(
            config.backends[config.inference.embedding_backend]
        )
    context_builder = ContextSnapshotBuilder(
        conversation_log_reader=log_writer,
        embedding_backend=embedding_backend,
        memory_store=PostgresConversationMemoryStore(config.database.dsn),
        session_summary_store=PostgresConversationSessionSummaryStore(
            config.database.dsn
        ),
        persona_store=PostgresPersonaSnapshotStore(config.database.dsn),
        calendar_store=PostgresCalendarEventStore(config.database.dsn),
        research_result_store=PostgresResearchResultStore(config.database.dsn),
        task_ledger_store=PostgresTaskLedgerStore(config.database.dsn),
    )
    router = InferenceRouter(config)
    backend = await router.select("conversation", "privacy")
    thinking_mode = ThinkFastMode(prompt_log_path=None)
    session_id = await session_store.create_session(
        device_id=args.device_id,
        start_reason="followup",
    )
    policy = ContextBuildPolicy.for_depth(args.depth)
    turn_results: list[dict[str, Any]] = []

    try:
        for index, text in enumerate(SAMPLE_TURNS, start=1):
            transcript = Transcript(
                text=text,
                device_id=args.device_id,
                speaker=args.speaker,
                audio_level_db=-20.0,
                recorded_at=datetime.now(UTC),
                is_final=True,
            )
            context_started = time.perf_counter()
            snapshot = await context_builder.build(
                text=transcript.text,
                speaker=transcript.speaker,
                device_id=transcript.device_id,
                active_session_id=session_id,
                policy=policy,
            )
            context_elapsed_ms = (time.perf_counter() - context_started) * 1000
            thinking_input = ThinkingInput(
                text=transcript.text,
                speaker=transcript.speaker,
                context=snapshot.recent_turns,
                emotion="neutral",
                device_id=transcript.device_id,
                context_snapshot=snapshot,
            )

            first_text_ms: float | None = None
            first_event_ms: float | None = None
            emotion: str | None = None
            reply_parts: list[str] = []
            started = time.perf_counter()
            async for event in thinking_mode.think(backend, thinking_input):
                elapsed_ms = (time.perf_counter() - started) * 1000
                if first_event_ms is None:
                    first_event_ms = elapsed_ms
                if event.type == "emotion":
                    emotion = event.value
                elif event.type == "text_delta":
                    if first_text_ms is None:
                        first_text_ms = elapsed_ms
                    reply_parts.append(event.value)
                elif event.type == "done":
                    break
            total_ms = (time.perf_counter() - started) * 1000
            reply_text = "".join(reply_parts).strip()
            emotion = emotion or "neutral"

            await log_writer.write_user_turn(
                transcript,
                participation_mode="invited",
                conversation_session_id=session_id,
            )
            await log_writer.write_tomoko_turn(
                text=reply_text,
                emotion=emotion,
                device_id=args.device_id,
                conversation_session_id=session_id,
            )
            turn_results.append(
                {
                    "turn": index,
                    "user": text,
                    "emotion": emotion,
                    "assistant": reply_text,
                    "first_event_ms": first_event_ms,
                    "first_text_ms": first_text_ms,
                    "total_ms": total_ms,
                    "context_elapsed_ms": context_elapsed_ms,
                    "snapshot_build_elapsed_ms": snapshot.build_elapsed_ms,
                    "snapshot_source_counts": snapshot.source_counts,
                    "recent_turn_count": len(snapshot.recent_turns),
                    "task_ledger_count": len(snapshot.task_ledger_entries),
                    "calendar_event_count": len(snapshot.calendar_events),
                    "research_result_count": len(snapshot.research_results),
                }
            )
    finally:
        await session_store.close_session(session_id, end_reason=args.label)

    first_text_values = [
        turn["first_text_ms"]
        for turn in turn_results
        if turn["first_text_ms"] is not None
    ]
    total_values = [turn["total_ms"] for turn in turn_results]
    result = {
        "label": args.label,
        "measured_at": datetime.now(UTC).isoformat(),
        "config": args.config,
        "depth": args.depth,
        "backend": backend.name,
        "device_id": args.device_id,
        "conversation_session_id": str(session_id),
        "turns": turn_results,
        "summary": {
            "turn_count": len(turn_results),
            "avg_first_text_ms": sum(first_text_values) / len(first_text_values),
            "avg_total_ms": sum(total_values) / len(total_values),
            "min_first_text_ms": min(first_text_values),
            "max_first_text_ms": max(first_text_values),
        },
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/central_realtime.toml")
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--depth", default="fast", choices=["fast", "normal", "deep"])
    parser.add_argument("--speaker", default="seijiro")
    parser.add_argument(
        "--device-id",
        default="codex-dflash-runtime-bench",
    )
    args = parser.parse_args()
    result = asyncio.run(_run(args))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
