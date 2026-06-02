"""
Smoke test: TomoroSession + real LLM + real PostgreSQL で timer/alarm の
create → ack reply → due 通知 の一連のフローを検証する。

実行:
    mise exec -- uv run python _tools/smoke_timer_alarm_session_flow.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import psycopg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.edge.pipeline.vad import VADProcessor  # noqa: E402
from server.gateway.thinking.fast import ThinkFastMode  # noqa: E402
from server.session import TomoroSession  # noqa: E402
from server.shared.config import NodeConfig  # noqa: E402
from server.shared.inference.router import InferenceRouter  # noqa: E402
from server.shared.models import ConnectedOutputState, SessionEvent, Transcript  # noqa: E402
from server.shared.timer_alarm import (  # noqa: E402
    PostgresTimerAlarmStore,
    TimerAlarmCommandRunner,
)

CONFIG_PATH = "config/central_realtime.toml"
DDL_PATH = "docker/postgres/init/017_timer_alarm.sql"
SMOKE_ENTRY_PREFIX = "smoke-timer-alarm-"


class QuietVad:
    def process_chunk(self, chunk: np.ndarray) -> float:
        del chunk
        return 0.0


async def run_timer_alarm_smoke(
    *,
    transcript_text: str = "ともこ、5分後に教えて",
    config_path: str = CONFIG_PATH,
    timeout_sec: float = 30.0,
    output_path: Path | None = None,
) -> dict[str, Any]:
    config = NodeConfig.load(config_path)
    dsn = config.database.dsn

    await _ensure_ddl(dsn)

    smoke_ids: list[str] = []
    try:
        result = await _run_smoke(
            transcript_text=transcript_text,
            config=config,
            dsn=dsn,
            timeout_sec=timeout_sec,
            smoke_ids=smoke_ids,
        )
    finally:
        await _cleanup(dsn, smoke_ids)

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return result


async def _run_smoke(
    *,
    transcript_text: str,
    config: NodeConfig,
    dsn: str,
    timeout_sec: float,
    smoke_ids: list[str],
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    store = PostgresTimerAlarmStore(dsn)
    router = InferenceRouter(config)

    session = TomoroSession(
        vad_processor=VADProcessor(vad=QuietVad(), silence_ms=400),
        send_event=events.append,
        router=router,
        thinking_mode=ThinkFastMode(prompt_log_path=None),
        timer_alarm_store=store,
        connected_output_state=ConnectedOutputState.single_client(device_id="smoke-desk"),
    )

    create_done = asyncio.Event()
    runner = TimerAlarmCommandRunner(store=store, session=session)

    async def handle_timer_alarm_transition(result) -> None:
        await runner.run_result(result)
        create_done.set()

    session.set_timer_alarm_transition_handler(handle_timer_alarm_transition)

    # --- Phase 1: transcript → intent detect → create ---
    await session.process_transcript(_transcript(transcript_text))
    await session._wait_for_reply_task()

    try:
        await asyncio.wait_for(create_done.wait(), timeout=timeout_sec)
    except TimeoutError:
        pass

    phase1_events = list(events)
    events.clear()

    # Identify created entry
    create_recorded = next(
        (e for e in phase1_events if e.get("type") == "timer_alarm_create_recorded"),
        None,
    )
    entry_id = (create_recorded or {}).get("entry_id") if create_recorded else None
    if isinstance(entry_id, str) and entry_id:
        smoke_ids.append(entry_id)

    ack_reply_text = "".join(
        str(e.get("delta"))
        for e in phase1_events
        if e.get("type") == "reply_text"
    )

    # --- Phase 2: due notification on a fresh ambient session ---
    # Simulate the timer worker: claim the scheduled row so status → 'due',
    # then post timer_due to a fresh ambient session (user not engaged).
    claimed_entries: list[Any] = []
    if entry_id:
        from datetime import timedelta  # noqa: PLC0415

        overdue_time = datetime.now(UTC) + timedelta(minutes=10)
        claimed_entries = await store.claim_due(
            worker_id="smoke-worker", now=overdue_time, limit=10
        )

    due_events: list[dict[str, Any]] = []
    due_session = TomoroSession(
        vad_processor=VADProcessor(vad=QuietVad(), silence_ms=400),
        send_event=due_events.append,
        router=InferenceRouter(config),
        thinking_mode=ThinkFastMode(prompt_log_path=None),
        timer_alarm_store=store,
        connected_output_state=ConnectedOutputState.single_client(device_id="smoke-desk"),
    )
    claimed_entry = next(
        (e for e in claimed_entries if e.entry_id == entry_id), None
    )
    due_label = claimed_entry.label if claimed_entry else "5分タイマー"
    due_kind = claimed_entry.kind if claimed_entry else "timer"
    due_transition = await due_session.post_event(
        SessionEvent(
            type="timer_due",
            payload={
                "entry_id": entry_id or "smoke-synthetic-due",
                "label": due_label,
                "kind": due_kind,
                "device_id": "smoke-desk",
            },
        )
    )
    await due_session.send_transition_emissions(due_transition)
    await due_session._run_internal_commands(due_transition.commands)
    await due_session._wait_for_reply_task()

    due_reply_text = "".join(
        str(e.get("delta"))
        for e in due_events
        if e.get("type") == "reply_text"
    )

    # --- Verify DB state: should be 'notified' after full lifecycle ---
    db_row = await _read_db_row(dsn, entry_id) if entry_id else None

    # --- Phase 3: alarm create (reuse existing session) ---
    events.clear()
    alarm_done = asyncio.Event()

    async def handle_alarm_transition(result) -> None:
        await runner.run_result(result)
        alarm_done.set()

    session.set_timer_alarm_transition_handler(handle_alarm_transition)

    await session.process_transcript(_transcript("ともこ、明日の9時に起こして"))
    await session._wait_for_reply_task()
    try:
        await asyncio.wait_for(alarm_done.wait(), timeout=timeout_sec)
    except TimeoutError:
        pass

    alarm_events = list(events)
    alarm_create_recorded = next(
        (e for e in alarm_events if e.get("type") == "timer_alarm_create_recorded"),
        None,
    )
    if alarm_create_recorded and isinstance(alarm_create_recorded.get("entry_id"), str):
        smoke_ids.append(alarm_create_recorded["entry_id"])

    alarm_ack_text = "".join(
        str(e.get("delta"))
        for e in alarm_events
        if e.get("type") == "reply_text"
    )

    ok = bool(
        create_recorded is not None
        and ack_reply_text
        and due_transition.emissions
        and alarm_create_recorded is not None
        and alarm_ack_text
    )

    return {
        "ok": ok,
        "transcript_text": transcript_text,
        # Phase 1
        "phase1_event_types": [str(e.get("type")) for e in phase1_events],
        "timer_request_accepted": any(
            e.get("type") == "timer_alarm_request_accepted" for e in phase1_events
        ),
        "timer_create_recorded": create_recorded is not None,
        "timer_entry_id": entry_id,
        "timer_due_at": (create_recorded or {}).get("due_at") if create_recorded else None,
        "ack_reply_text": ack_reply_text,
        "ack_reply_has_content": bool(ack_reply_text),
        # Phase 2 - due notification
        "due_event_type": due_transition.emissions[0].type if due_transition.emissions else None,
        "due_notice_command_fired": any(
            c.type == "start_timer_alarm_due_notice" for c in due_transition.commands
        ),
        "due_reply_text": due_reply_text,
        "due_reply_has_content": bool(due_reply_text),
        "db_row_status": db_row.get("status") if db_row else None,
        # Phase 3 - alarm
        "alarm_request_accepted": any(
            e.get("type") == "timer_alarm_request_accepted" for e in alarm_events
        ),
        "alarm_create_recorded": alarm_create_recorded is not None,
        "alarm_entry_id": (
            (alarm_create_recorded or {}).get("entry_id") if alarm_create_recorded else None
        ),
        "alarm_due_at": (
            (alarm_create_recorded or {}).get("due_at") if alarm_create_recorded else None
        ),
        "alarm_ack_reply_has_content": bool(alarm_ack_text),
        "alarm_ack_reply_text": alarm_ack_text,
    }


async def _ensure_ddl(dsn: str) -> None:
    ddl = Path(DDL_PATH).read_text(encoding="utf-8")
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(ddl)
        await conn.commit()


async def _read_db_row(dsn: str, entry_id: str) -> dict[str, Any] | None:
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, kind, label, status, due_at, source"
                " FROM timer_alarm_entries WHERE id = %s",
                (entry_id,),
            )
            row = await cur.fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "kind": row[1],
        "label": row[2],
        "status": row[3],
        "due_at": row[4].isoformat() if row[4] else None,
        "source": row[5],
    }


async def _cleanup(dsn: str, entry_ids: list[str]) -> None:
    if not entry_ids:
        return
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM timer_alarm_entries WHERE id = ANY(%s)",
                (entry_ids,),
            )
        await conn.commit()


def _transcript(text: str) -> Transcript:
    return Transcript(
        text=text,
        device_id="smoke-desk",
        speaker=None,
        audio_level_db=-20.0,
        recorded_at=datetime.now(UTC),
        is_final=True,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a TomoroSession timer/alarm e2e smoke: real DB + real LLM.\n"
            "Phases: (1) voice create, (2) due notice simulation, (3) alarm create."
        )
    )
    parser.add_argument(
        "--transcript",
        default="ともこ、5分後に教えて",
        help="Finalized transcript text for timer create.",
    )
    parser.add_argument("--timeout-sec", type=float, default=30.0)
    parser.add_argument("--config", default=CONFIG_PATH, help="Path to TOML config.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("logs/timer-alarm-smoke.json"),
    )
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    summary = await run_timer_alarm_smoke(
        transcript_text=args.transcript,
        config_path=args.config,
        timeout_sec=args.timeout_sec,
        output_path=args.output,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
