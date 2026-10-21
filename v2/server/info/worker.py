from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from server.info.main import _upsert_world_fixture
from server.tomoko.sense import (
    SENSE_KIND_WORLD_SEARCH,
    SENSE_STATUS_FAILED,
    claim_pending_sense_requests,
    complete_sense_request_sql,
)

SearchBackend = Callable[[str], list[Mapping[str, Any]]]


@dataclass(slots=True)
class WorldSearchTickResult:
    requests_claimed: int = 0
    requests_done: int = 0
    requests_failed: int = 0
    items_upserted: int = 0


def create_command_search_backend(
    env_key: str = "TOMOKO_V2_WORLD_SEARCH_CMD",
) -> SearchBackend | None:
    """env のコマンドを検索 backend にする。

    コマンドは引数に query を受け取り、stdout に
    {"items": [{"source_key": ..., "text": ..., "confidence": ...}]} を返す。
    MCP ツールを繋ぐ場合はこのコマンド境界に薄いクライアントを置く。
    """
    command = os.environ.get(env_key)
    if not command:
        return None
    argv = shlex.split(command)

    def search(query: str) -> list[Mapping[str, Any]]:
        completed = subprocess.run(
            [*argv, query],
            capture_output=True,
            text=True,
            timeout=float(os.environ.get("TOMOKO_V2_WORLD_SEARCH_TIMEOUT_SEC", "150")),
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"world search command failed rc={completed.returncode}: "
                f"{completed.stderr.strip()[:200]}"
            )
        payload = json.loads(completed.stdout)
        return list(payload.get("items", []))

    return search


async def process_world_search_sense_requests(
    conn: Any,
    *,
    search: SearchBackend | None = None,
    limit: int = 2,
) -> WorldSearchTickResult:
    requests = await claim_pending_sense_requests(
        conn,
        kind=SENSE_KIND_WORLD_SEARCH,
        limit=limit,
    )
    result = WorldSearchTickResult(requests_claimed=len(requests))
    if not requests:
        return result
    backend = search or create_command_search_backend()
    for request in requests:
        if backend is None:
            result.requests_failed += 1
            command = complete_sense_request_sql(
                request.id,
                result={"error": "no_backend"},
                status=SENSE_STATUS_FAILED,
            )
            await conn.execute(command.query, command.params)
            continue
        try:
            items = await asyncio.to_thread(backend, request.query)
        except Exception as exc:
            result.requests_failed += 1
            command = complete_sense_request_sql(
                request.id,
                result={"error": type(exc).__name__, "message": str(exc)[:200]},
                status=SENSE_STATUS_FAILED,
            )
            await conn.execute(command.query, command.params)
            continue
        texts: list[str] = []
        for index, item in enumerate(items):
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            source_key = str(
                item.get("source_key", f"sense-{request.id.hex[:8]}-{index}")
            )
            await _upsert_world_fixture(
                conn,
                source="world_search",
                source_key=source_key,
                raw_text=text,
                title=str(item.get("title", source_key)),
                body=text,
                summary=text,
                confidence=float(item.get("confidence", 0.7)),
                flags={},
                metadata={"sense_request_id": str(request.id), "query": request.query},
            )
            texts.append(text)
            result.items_upserted += 1
        command = complete_sense_request_sql(
            request.id,
            result={"texts": texts, "items": len(texts), "query": request.query},
        )
        await conn.execute(command.query, command.params)
        result.requests_done += 1
    return result
