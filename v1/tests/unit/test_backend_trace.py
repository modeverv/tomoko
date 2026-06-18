from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pytest

from server.edge.pipeline.stt import MlxWhisperSTT
from server.shared.inference.embedding.sentence_transformer import (
    SentenceTransformerEmbeddingBackend,
)
from server.shared.inference.trace import (
    backend_trace_path,
    chat_stream_with_trace_role,
    trace_backend_call,
)
from server.shared.models import SpeechSegment


class LegacyBackend:
    name = "legacy"

    async def chat_stream(self, system_prompt: str, messages: list[dict[str, str]]):
        del system_prompt, messages
        yield "ok"


class TraceRoleBackend:
    name = "traceable"

    async def chat_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        *,
        trace_role: str | None = None,
    ):
        del system_prompt, messages
        yield trace_role or "missing"


@pytest.mark.unit
def test_trace_backend_call_writes_one_json_object_per_line(tmp_path, monkeypatch) -> None:
    trace_path = tmp_path / "backend-trace.jsonl"
    monkeypatch.setenv("TOMOKO_BACKEND_TRACE_FILE", str(trace_path))

    trace_backend_call(
        event="start",
        kind="llm",
        role="conversation",
        backend="lmstudio_gemma4_e4b",
        model="gemma-4-e4b-it-mlx",
        request_id="req-1",
    )
    trace_backend_call(
        event="done",
        kind="llm",
        role="conversation",
        backend="lmstudio_gemma4_e4b",
        model="gemma-4-e4b-it-mlx",
        request_id="req-1",
        total_ms=12.3,
        chunk_count=2,
    )

    rows = [json.loads(line) for line in trace_path.read_text().splitlines()]

    assert backend_trace_path() == trace_path
    assert rows[0]["trace"] == "tomoko_backend_call"
    assert rows[0]["event"] == "start"
    assert rows[0]["role"] == "conversation"
    assert rows[1]["event"] == "done"
    assert rows[1]["total_ms"] == 12.3
    assert rows[1]["chunk_count"] == 2
    assert all("ts" in row for row in rows)


@pytest.mark.unit
async def test_chat_stream_with_trace_role_keeps_legacy_backends_compatible() -> None:
    chunks = [
        chunk
        async for chunk in chat_stream_with_trace_role(
            LegacyBackend(),
            "system",
            [{"role": "user", "content": "hi"}],
            trace_role="conversation",
        )
    ]

    assert chunks == ["ok"]


@pytest.mark.unit
async def test_chat_stream_with_trace_role_passes_role_when_supported() -> None:
    chunks = [
        chunk
        async for chunk in chat_stream_with_trace_role(
            TraceRoleBackend(),
            "system",
            [{"role": "user", "content": "hi"}],
            trace_role="conversation",
        )
    ]

    assert chunks == ["conversation"]


@pytest.mark.unit
async def test_stt_backend_writes_jsonl_trace(tmp_path, monkeypatch) -> None:
    trace_path = tmp_path / "backend-trace.jsonl"
    monkeypatch.setenv("TOMOKO_BACKEND_TRACE_FILE", str(trace_path))

    def fake_transcribe(_audio_path: str, **_kwargs: object) -> dict[str, str]:
        return {"text": "ともこ、聞こえます"}

    monkeypatch.setitem(
        sys.modules,
        "mlx_whisper",
        SimpleNamespace(transcribe=fake_transcribe),
    )
    transcriber = MlxWhisperSTT(model_name="mlx-community/whisper-small-mlx")
    segment = SpeechSegment(
        audio=np.zeros(1600, dtype=np.float32),
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        device_id="local",
        vad_confidence=0.9,
    )

    transcript = await transcriber.transcribe(segment)

    rows = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert transcript.text == "ともこ、聞こえます"
    assert [row["event"] for row in rows] == ["start", "done"]
    assert rows[0]["kind"] == "stt"
    assert rows[0]["role"] == "stt"
    assert rows[0]["queue_key"] == "local_mlx"
    assert rows[1]["text_len"] == len("ともこ、聞こえます")


@pytest.mark.unit
async def test_embedding_backend_writes_jsonl_trace(tmp_path, monkeypatch) -> None:
    trace_path = tmp_path / "backend-trace.jsonl"
    monkeypatch.setenv("TOMOKO_BACKEND_TRACE_FILE", str(trace_path))

    backend = SentenceTransformerEmbeddingBackend(
        name="fake_embedding",
        model="fake-model",
        dimensions=3,
    )
    monkeypatch.setattr(backend, "_embed_sync", lambda _text: [0.1, 0.2, 0.3])

    embedding = await backend.embed_query("こんにちは")

    rows = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert embedding == [0.1, 0.2, 0.3]
    assert [row["event"] for row in rows] == ["start", "done"]
    assert rows[0]["kind"] == "embedding"
    assert rows[0]["role"] == "embedding_query"
    assert rows[0]["backend"] == "fake_embedding"
    assert rows[1]["dimensions"] == 3
