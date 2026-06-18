from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

WorldObservationStatus = Literal["completed", "failed", "needs_human", "timeout"]
Runner = Callable[[list[str], str, float, Path | None], Awaitable[str]]

WORLD_OBSERVE_TOOL_NAME = "world.observe"
DEFAULT_COLLECTION_HOUR = 9
DEFAULT_COLLECTION_TZ = "Asia/Tokyo"
REQUIRED_BODY_MARKERS = ("外界観測レポート",)
REQUIRED_BODY_LABELS = ("事実", "推測・含意", "source_hint")
REQUIRED_TOPIC_MARKERS = (
    "news",
    "economy",
    "technology",
    "culture",
    "local_life",
    "ai",
    "local_inference",
)


@dataclass(frozen=True, slots=True)
class WorldObservationOperatorRequest:
    prompt: str
    title: str
    observed_at: str
    locale: str = "ja-JP"

    def to_json(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt.strip(),
            "title": self.title.strip(),
            "observed_at": self.observed_at.strip(),
            "locale": self.locale,
        }


@dataclass(frozen=True, slots=True)
class WorldObservationOperatorResult:
    status: WorldObservationStatus
    title: str
    observed_at: str
    provider: str = "perplexity"
    markdown_text: str = ""
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    provider_trace_id: str | None = None
    raw_artifact_path: str | None = None
    error_reason: str | None = None

    def is_completed(self) -> bool:
        return self.status == "completed" and bool(self.markdown_text.strip())


class WorldObservationMcpClient:
    def __init__(
        self,
        *,
        command: tuple[str, ...],
        timeout_sec: float = 180.0,
        cwd: Path | None = None,
        runner: Runner | None = None,
    ) -> None:
        self.command = command
        self.timeout_sec = timeout_sec
        self.cwd = cwd
        self.runner = runner or _run_subprocess

    async def observe(
        self,
        request: WorldObservationOperatorRequest,
    ) -> WorldObservationOperatorResult:
        stdin_text = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": WORLD_OBSERVE_TOOL_NAME,
                    "arguments": request.to_json(),
                },
            },
            ensure_ascii=False,
        )
        try:
            stdout = await self.runner(
                list(self.command),
                stdin_text + "\n",
                self.timeout_sec,
                self.cwd,
            )
        except TimeoutError as exc:
            return WorldObservationOperatorResult(
                status="timeout",
                title=request.title,
                observed_at=request.observed_at,
                error_reason=str(exc),
            )
        except OSError as exc:
            return WorldObservationOperatorResult(
                status="failed",
                title=request.title,
                observed_at=request.observed_at,
                error_reason=str(exc),
            )
        return parse_world_observation_mcp_response(
            stdout,
            fallback_title=request.title,
            fallback_observed_at=request.observed_at,
        )


def create_default_world_observation_mcp_client() -> WorldObservationMcpClient:
    command_env = os.environ.get("TOMOKO_WORLD_OBSERVATION_MCP_COMMAND")
    if command_env:
        command = tuple(command_env.split())
        cwd = None
    else:
        command = ("uv", "run", "tomoko-research-mcp")
        cwd = _default_operator_dir()
    timeout_sec = float(os.environ.get("TOMOKO_WORLD_OBSERVATION_MCP_TIMEOUT_SEC", "240"))
    return WorldObservationMcpClient(command=command, timeout_sec=timeout_sec, cwd=cwd)


def build_daily_world_observation_request(
    *,
    prompt_template: str,
    collection_date: str,
    observed_at: str | None = None,
    locale: str = "ja-JP",
) -> WorldObservationOperatorRequest:
    observed_at_text = observed_at or default_observed_at_for_date(collection_date)
    title = f"world_observation_{collection_date}"
    prompt = _rewrite_daily_prompt_template(
        prompt_template,
        collection_date=collection_date,
        observed_at=observed_at_text,
        title=title,
    )
    return WorldObservationOperatorRequest(
        prompt=prompt,
        title=title,
        observed_at=observed_at_text,
        locale=locale,
    )


def save_world_observation_markdown(
    result: WorldObservationOperatorResult,
    *,
    output_dir: Path,
    collection_date: str,
) -> Path:
    if not result.is_completed():
        raise ValueError(f"world observation result is not completed: {result.status}")
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{collection_date}-world-observation.md"
    body = _strip_existing_frontmatter(result.markdown_text).strip()
    issue = validate_world_observation_provider_body(body)
    if issue is not None:
        raise ValueError(issue)
    path.write_text(_format_raw_markdown(result, body), encoding="utf-8")
    return path


def default_observed_at_for_date(collection_date: str) -> str:
    year, month, day = (int(part) for part in collection_date.split("-"))
    tz = ZoneInfo(DEFAULT_COLLECTION_TZ)
    return datetime(year, month, day, DEFAULT_COLLECTION_HOUR, 0, tzinfo=tz).isoformat()


def validate_world_observation_provider_body(body: str) -> str | None:
    text = body.strip()
    lowered = text.lower()
    created_summary_markers = (
        "を作成・共有しました",
        "を作成しました",
        "構成の概要",
        "各 topic の内容サマリー",
    )
    if any(marker in text for marker in created_summary_markers):
        return "world observation body looks like a provider document summary"
    if len(text) < 1200:
        return f"world observation body is too short: chars={len(text)}"
    missing = [marker for marker in REQUIRED_BODY_MARKERS if marker.lower() not in lowered]
    if missing:
        return f"world observation body is missing required markers: {', '.join(missing)}"
    missing_labels = [label for label in REQUIRED_BODY_LABELS if not _has_label(text, label)]
    if missing_labels:
        return (
            "world observation body is missing required labels: "
            + ", ".join(missing_labels)
        )
    missing_topics = [
        topic for topic in REQUIRED_TOPIC_MARKERS if not _has_topic_heading(text, topic)
    ]
    if missing_topics:
        return (
            "world observation body is missing topic headings: "
            + ", ".join(missing_topics)
        )
    return None


def parse_world_observation_mcp_response(
    stdout: str,
    *,
    fallback_title: str,
    fallback_observed_at: str,
) -> WorldObservationOperatorResult:
    line = _last_json_line(stdout)
    if line is None:
        return WorldObservationOperatorResult(
            status="failed",
            title=fallback_title,
            observed_at=fallback_observed_at,
            error_reason="MCP response did not contain JSON",
        )
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        return WorldObservationOperatorResult(
            status="failed",
            title=fallback_title,
            observed_at=fallback_observed_at,
            error_reason=str(exc),
        )
    error = payload.get("error")
    if isinstance(error, dict):
        return WorldObservationOperatorResult(
            status="failed",
            title=fallback_title,
            observed_at=fallback_observed_at,
            error_reason=str(error.get("message") or "MCP error"),
        )
    result = payload.get("result")
    structured = result.get("structuredContent") if isinstance(result, dict) else None
    if not isinstance(structured, dict):
        return WorldObservationOperatorResult(
            status="failed",
            title=fallback_title,
            observed_at=fallback_observed_at,
            error_reason="MCP structuredContent was missing",
        )
    return _world_observation_result_from_payload(
        structured,
        fallback_title=fallback_title,
        fallback_observed_at=fallback_observed_at,
    )


async def _run_subprocess(
    command: list[str],
    stdin_text: str,
    timeout_sec: float,
    cwd: Path | None,
) -> str:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd is not None else None,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(stdin_text.encode("utf-8")),
            timeout=timeout_sec,
        )
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise TimeoutError(
            f"world observation MCP command timed out after {timeout_sec:.1f}s"
        ) from exc
    if process.returncode != 0:
        raise OSError(stderr.decode("utf-8", errors="replace").strip())
    return stdout.decode("utf-8", errors="replace")


def _world_observation_result_from_payload(
    payload: dict[str, Any],
    *,
    fallback_title: str,
    fallback_observed_at: str,
) -> WorldObservationOperatorResult:
    status = str(payload.get("status") or "failed")
    if status not in {"completed", "failed", "needs_human", "timeout"}:
        status = "failed"
    return WorldObservationOperatorResult(
        status=status,  # type: ignore[arg-type]
        title=str(payload.get("title") or fallback_title),
        observed_at=str(payload.get("observed_at") or fallback_observed_at),
        provider=str(payload.get("provider") or "perplexity"),
        markdown_text=str(payload.get("markdown_text") or ""),
        fetched_at=_parse_datetime(payload.get("fetched_at")),
        provider_trace_id=_optional_str(payload.get("provider_trace_id")),
        raw_artifact_path=_optional_str(payload.get("raw_artifact_path")),
        error_reason=_optional_str(payload.get("error_reason")),
    )


def _format_raw_markdown(result: WorldObservationOperatorResult, body: str) -> str:
    return (
        "---\n"
        "schema_version: 1\n"
        "kind: world_observation_batch\n"
        f"generated_by: {result.provider}\n"
        f"observed_at: {result.observed_at}\n"
        "language: ja\n"
        "topics: [news, economy, technology, culture, local_life, ai, local_inference]\n"
        "source_policy: public_web_summary_only\n"
        "collection_prompt_version: daily_world_observation_v1\n"
        "---\n"
        f"{body}\n"
    )


def _rewrite_daily_prompt_template(
    prompt_template: str,
    *,
    collection_date: str,
    observed_at: str,
    title: str,
) -> str:
    prompt = re.sub(r"world_observation_\d{4}-\d{2}-\d{2}", title, prompt_template)
    prompt = re.sub(
        r"observed_at:\s*[^\n]+",
        f"observed_at: {observed_at}",
        prompt,
    )
    prompt = re.sub(r"\d{4}-\d{2}-\d{2}", collection_date, prompt)
    return prompt


def _strip_existing_frontmatter(text: str) -> str:
    stripped = _strip_provider_preamble(text.lstrip())
    if not stripped.startswith("---\n"):
        schema_index = stripped.find("schema_version:")
        heading_index = stripped.find("外界観測レポート")
        if schema_index < 0 or (heading_index >= 0 and heading_index < schema_index):
            return stripped
        candidate = stripped[schema_index:]
        header, separator, body = candidate.partition("\n\n")
        if separator and "collection_prompt_version:" in header:
            return body.lstrip("\n")
        return stripped
    end = stripped.find("\n---", 4)
    if end < 0:
        return stripped
    body_start = end + len("\n---")
    if body_start < len(stripped) and stripped[body_start] == "\n":
        body_start += 1
    return stripped[body_start:].lstrip("\n")


def _strip_provider_preamble(text: str) -> str:
    preambles = (
        "以下が本文です。",
        "以下が本文です:",
        "以下が本文です：",
    )
    for preamble in preambles:
        if text.startswith(preamble):
            return text[len(preamble) :].lstrip("\n")
    return text


def _has_topic_heading(text: str, topic: str) -> bool:
    return re.search(rf"(?im)^(?:#+\s*)?{re.escape(topic)}\b", text) is not None


def _has_label(text: str, label: str) -> bool:
    return re.search(rf"(?im)^{re.escape(label)}\s*[:：]", text) is not None


def _default_operator_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "tomoko-research-operator"


def _last_json_line(stdout: str) -> str | None:
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
