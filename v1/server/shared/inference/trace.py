from __future__ import annotations

import inspect
import json
import logging
import os
from collections.abc import AsyncGenerator
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TRACE_NAME = "tomoko_backend_call"
DEFAULT_TRACE_PATH = Path("logs/backend-trace.jsonl")


def backend_trace_path() -> Path:
    return Path(os.environ.get("TOMOKO_BACKEND_TRACE_FILE", DEFAULT_TRACE_PATH))


def trace_backend_call(
    *,
    event: str,
    kind: str,
    role: str,
    backend: str,
    model: str | None = None,
    request_id: str | None = None,
    **fields: object,
) -> None:
    row: dict[str, object] = {
        "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "trace": TRACE_NAME,
        "event": event,
        "kind": kind,
        "role": role,
        "backend": backend,
    }
    if model is not None:
        row["model"] = model
    if request_id is not None:
        row["request_id"] = request_id
    row.update({key: value for key, value in fields.items() if value is not None})

    path = backend_trace_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            f.write("\n")
    except Exception:
        logger.exception("failed to write backend trace jsonl path=%s", path)


async def chat_stream_with_trace_role(
    backend: Any,
    system_prompt: str,
    messages: list[dict[str, str]],
    *,
    max_tokens: int | None = None,
    trace_role: str,
) -> AsyncGenerator[str, None]:
    chat_stream = backend.chat_stream
    kwargs: dict[str, Any] = {}
    if max_tokens is not None and _accepts_keyword(chat_stream, "max_tokens"):
        kwargs["max_tokens"] = max_tokens
    if _accepts_keyword(chat_stream, "trace_role"):
        kwargs["trace_role"] = trace_role
    if kwargs:
        async for chunk in chat_stream(
            system_prompt,
            messages,
            **kwargs,
        ):
            yield chunk
        return

    async for chunk in chat_stream(system_prompt, messages):
        yield chunk


async def chat_stream_structured_with_trace_role(
    backend: Any,
    system_prompt: str,
    messages: list[dict[str, str]],
    *,
    json_schema: dict[str, Any],
    max_tokens: int | None = None,
    trace_role: str,
) -> AsyncGenerator[str, None]:
    chat_stream_structured = backend.chat_stream_structured
    kwargs: dict[str, Any] = {"json_schema": json_schema}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if _accepts_keyword(chat_stream_structured, "trace_role"):
        kwargs["trace_role"] = trace_role

    async for chunk in chat_stream_structured(system_prompt, messages, **kwargs):
        yield chunk


def _accepts_keyword(callable_obj: object, name: str) -> bool:
    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return False
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            return True
        if parameter.kind in (
            inspect.Parameter.KEYWORD_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ) and parameter.name == name:
            return True
    return False
