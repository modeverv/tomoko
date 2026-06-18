from __future__ import annotations

import json
import subprocess
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from server.hot_path.app import app
from server.hot_path.model_executor import PromptExecutor, StaticChatBackend, StaticWavTtsBackend
from server.shared.logging import JsonlLogger
from server.shared.notify import build_notify_message, notify_sql, parse_id_payload
from server.shared.process import Heartbeat, HeartbeatWriter
from server.shared.schemas import SCREEN_ACTIVITY_FIXED_LINE_SCHEMA, parse_fixed_line_output
from server.user_status import ocr_runtime
from server.user_status.ocr_runtime import ocr_runtime_available, vision_ocr_text

pytestmark = pytest.mark.unit


def test_notify_payload_is_id_only_and_channel_limited(
    capsys: pytest.CaptureFixture[str],
) -> None:
    event_id = UUID("00000000-0000-0000-0000-000000000123")
    message = build_notify_message("v2_prompt_request", event_id)
    assert message.payload == event_id
    assert parse_id_payload(str(event_id)) == event_id
    with pytest.raises(ValueError):
        parse_id_payload(json.dumps({"id": str(event_id)}))
    with pytest.raises(ValueError):
        build_notify_message("v2_not_allowed", event_id)
    sql, params = notify_sql("v2_candidate", event_id)
    assert "pg_notify" in sql
    assert params["payload"] == str(event_id)
    captured = capsys.readouterr()
    assert "notify_send" in captured.out
    assert "v2_candidate" in captured.out


def test_fixed_line_parser_requires_small_vlm_schema() -> None:
    parsed = parse_fixed_line_output(
        """
        SCREEN_ACTIVITY_LABEL=coding_or_terminal
        CONFIDENCE=0.8
        WATCHING_VIDEO=0
        PLAYING_GAME=0
        """,
        SCREEN_ACTIVITY_FIXED_LINE_SCHEMA,
    )
    assert parsed["SCREEN_ACTIVITY_LABEL"] == "coding_or_terminal"
    with pytest.raises(ValueError):
        parse_fixed_line_output("SCREEN_ACTIVITY_LABEL=x", SCREEN_ACTIVITY_FIXED_LINE_SCHEMA)


class _FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, params: tuple[object, ...]) -> None:
        self.calls.append((query, params))


class _ConnectionContext:
    def __init__(self, conn: _FakeConnection) -> None:
        self.conn = conn

    async def __aenter__(self) -> _FakeConnection:
        return self.conn

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakePool:
    def __init__(self) -> None:
        self.conn = _FakeConnection()

    def connection(self) -> _ConnectionContext:
        return _ConnectionContext(self.conn)


@pytest.mark.asyncio
async def test_heartbeat_writer_upserts_process_state() -> None:
    pool = _FakePool()
    await HeartbeatWriter(pool).write(
        Heartbeat(process_name="fake", process_kind="unit", details={"ready": True})
    )
    assert pool.conn.calls
    query, params = pool.conn.calls[0]
    assert "ON CONFLICT" in query
    assert params[0] == "fake"


def test_jsonl_logger_writes_structured_event(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    JsonlLogger(path).log("state_transition", state="listening")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["event"] == "state_transition"
    assert payload["state"] == "listening"


def test_makefile_exposes_v2_runtime_targets_in_order() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")
    assert "v2-runtime tmux-runtime:" in makefile
    assert "llm-run:" in makefile
    assert "semantic-e2b-run:" in makefile
    assert "voicevox-run:" in makefile
    assert "v2-runtime-ready:" in makefile
    assert "TOMOKO_V2_SEMANTIC_LLM_URL ?= http://127.0.0.1:8083" in makefile
    assert "TOMOKO_V2_SEMANTIC_LLM=1" in makefile
    assert "v2-ocr-smoke" in makefile
    assert "v2-conversation-smoke:" in makefile
    assert "v2-scheduler-conversation-smoke:" in makefile
    assert "v2-db-split-smoke:" in makefile
    assert "v2-say-latency-smoke:" in makefile
    assert "v2-scheduler-say-latency-smoke:" in makefile
    assert "v2-five-turn-smoke:" in makefile
    assert "v2-semantic-early-smoke:" in makefile
    assert "v2-scheduler-report:" in makefile
    assert "TOMOKO_V2_VOICEVOX_SPEED ?= 1.5" in makefile
    assert makefile.index("-n llm-run") < makefile.index("-n semantic-e2b")
    assert makefile.index("-n semantic-e2b") < makefile.index("-n hot-path")
    assert "tmux send-keys -t $(TMUX_SESSION):hot-path C-c" in makefile
    assert "tmux send-keys -t $(TMUX_SESSION):semantic-e2b C-c" in makefile
    assert "v2-report-latest:" in makefile


def test_db_split_runtime_reuses_process_lifetime_connections() -> None:
    hot_path_source = Path("server/hot_path/db_conversation.py").read_text(encoding="utf-8")
    worker_source = Path("server/tomoko/db_worker.py").read_text(encoding="utf-8")

    assert hot_path_source.count("AsyncConnection.connect") == 2
    assert "async def _ensure_connections" in hot_path_source
    assert "async def _load_speech_order" in hot_path_source
    assert "AsyncConnection.connect" not in hot_path_source[
        hot_path_source.index("async def _load_speech_order") :
        hot_path_source.index("def create_default_db_split_conversation")
    ]

    assert worker_source.count("AsyncConnection.connect") == 2
    assert "async def _open_connections" in worker_source
    assert "AsyncConnection.connect" not in worker_source[
        worker_source.index("async def process_observation_id") :
        worker_source.index("async def _open_listener")
    ]


def test_five_turn_smoke_has_five_default_turns() -> None:
    source = Path("scripts/v2_five_turn_smoke.py").read_text(encoding="utf-8")

    assert "class FiveTurnResult" in source
    assert "DEFAULT_TURNS" in source
    assert source.count('"トモコ、短く返事して。"') == 1
    assert source.count('"最後に、今の状態を短くまとめて。"') == 1
    assert "average_first_audio_ms" in source
    assert "p95_first_audio_ms" in source
    assert "llm_prompt" in source


def test_ocr_runtime_availability_reports_expected_keys() -> None:
    availability = ocr_runtime_available()
    assert set(availability) == {"screencapture", "vision_ocr", "tesseract", "osascript"}


def test_vision_ocr_text_uses_sidecar_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = tmp_path / "screen.png"
    image.write_bytes(b"png")
    command = tmp_path / "vision-ocr"
    command.write_text("#!/bin/sh\n", encoding="utf-8")

    def fake_ensure() -> Path:
        return command

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert args[:3] == [str(command), "--image", str(image)]
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps({"text": "Vision text"}),
            stderr="",
        )

    monkeypatch.setattr(ocr_runtime, "ensure_vision_ocr_command", fake_ensure)
    monkeypatch.setattr(ocr_runtime.subprocess, "run", fake_run)

    assert vision_ocr_text(image) == "Vision text"


def test_hot_path_websocket_uses_prompt_executor_for_text_prompt() -> None:
    app.state.prompt_executor = PromptExecutor(
        StaticChatBackend(["うん"]),
        StaticWavTtsBackend([b"RIFFxxxxWAVEdata"]),
    )
    try:
        with TestClient(app).websocket_connect("/ws") as websocket:
            ready = json.loads(websocket.receive_text())
            assert ready["type"] == "ready"

            websocket.send_json({"type": "prompt", "text": "トモコ、返事して"})

            prompt = json.loads(websocket.receive_text())
            delta = json.loads(websocket.receive_text())
            complete = json.loads(websocket.receive_text())
            tts_result = json.loads(websocket.receive_text())
            audio = websocket.receive_bytes()
            audio_complete = json.loads(websocket.receive_text())
            done = json.loads(websocket.receive_text())
    finally:
        del app.state.prompt_executor

    assert prompt["type"] == "llm_prompt"
    assert prompt["prompt_text"] == "トモコ、返事して"
    assert prompt["prompt_chars"] == len("トモコ、返事して")
    assert delta["type"] == "model_delta"
    assert delta["text_delta"] == "うん"
    assert complete["type"] == "model_complete"
    assert complete["text"] == "うん"
    assert tts_result["type"] == "tts_result"
    assert tts_result["text"] == "うん"
    assert tts_result["audio_chunks"] == 1
    assert audio == b"RIFFxxxxWAVEdata"
    assert audio_complete["type"] == "audio_complete"
    assert done["type"] == "prompt_complete"


def test_client_renders_stt_and_tts_timeline() -> None:
    index = Path("client/index.html").read_text(encoding="utf-8")
    script = Path("client/main.js").read_text(encoding="utf-8")
    styles = Path("client/styles.css").read_text(encoding="utf-8")

    assert 'id="timeline"' in index
    assert 'id="timeline-items"' in index
    assert "appendTimelineItem" in script
    assert 'payload.type === "transcript" && payload.is_final' in script
    assert 'if (text) appendTimelineItem("stt", text);' in script
    assert 'payload.type === "tts_result"' in script
    assert "console.log" in script
    assert ".timeline-item" in styles


def test_ddl_has_core_tables_and_id_only_notify_function() -> None:
    ddl = Path("docker/postgres/init/100_v2_core.sql").read_text(encoding="utf-8")
    for table in [
        "v2_process_heartbeats",
        "v2_stt_observations",
        "v2_utterances",
        "v2_conversation_sessions",
        "v2_prompt_requests",
        "v2_model_output_events",
        "v2_audio_output_events",
        "v2_speech_orders",
        "v2_speech_scheduler_decisions",
        "v2_semantic_saturation_observations",
        "v2_floor_observations",
        "v2_speech_decisions",
        "v2_context_snapshots",
        "v2_candidates",
        "v2_user_status_observations",
        "v2_world_documents",
        "v2_world_items",
        "v2_world_interpretations",
        "v2_session_summaries",
        "v2_summary_embeddings",
        "v2_eval_turns",
        "v2_eval_scores",
    ]:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in ddl
    assert "PERFORM pg_notify(channel_name, event_id::text)" in ddl
    assert "'v2_speech_order'" in ddl
    assert "json" not in ddl.split("CREATE OR REPLACE FUNCTION v2_notify_id", 1)[1].lower()


def test_notify_accepts_speech_order_channel() -> None:
    event_id = UUID("00000000-0000-0000-0000-000000000321")
    message = build_notify_message("v2_speech_order", event_id)
    assert message.payload == event_id
