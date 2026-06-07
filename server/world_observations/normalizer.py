from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Protocol

from server.shared.inference.trace import (
    chat_stream_with_trace_role,
)
from server.shared.models import (
    WorldObservationNormalizedBatch,
    WorldObservationNormalizedItem,
    WorldObservationNormalizeTrace,
    WorldObservationParseIssue,
    WorldObservationRawDocument,
)


class NormalizerBackend(Protocol):
    name: str

    async def chat_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
    ): ...


NORMALIZER_JSON_SCHEMA: dict[str, Any] = {
    "name": "world_observation_normalized_batch",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "topic": {"type": "string"},
                        "title": {"type": "string"},
                        "summary": {"type": "string"},
                        "source_hint": {"type": "string"},
                        "freshness": {
                            "type": "string",
                            "enum": ["breaking", "fresh", "recent", "stale", "unknown"],
                        },
                        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "raw_excerpt": {"type": "string"},
                        "item_json": {"type": "object", "additionalProperties": True},
                        "parse_notes": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "topic",
                        "title",
                        "summary",
                        "source_hint",
                        "freshness",
                        "confidence",
                        "raw_excerpt",
                        "item_json",
                        "parse_notes",
                    ],
                },
            }
        },
        "required": ["items"],
    },
}


NORMALIZER_SYSTEM_PROMPT = """\
あなたは Tomoko の外部観測 Markdown を構造化する background normalizer です。
raw Markdown は不安定な外部観測原稿であり、Tomoko が信じる事実ではありません。
本文をルールベースで無理に理解せず、観測項目を JSON に整理してください。

返答は JSON object だけにしてください。
items は最大 8 件にしてください。
raw Markdown 全体は DB に保存されるため、網羅よりも代表的な観測項目の抽出を優先してください。
schema:
{
  "items": [
    {
      "topic": "news | economy | technology | culture | local_life | ai | local_inference | other",
      "title": "短い題名",
      "summary": "日本語の短い要約",
      "source_hint": "本文中にある出典や手がかり。なければ unknown",
      "freshness": "breaking | fresh | recent | stale | unknown",
      "confidence": 0.0-1.0,
      "raw_excerpt": "本文からの短い抜粋",
      "item_json": {"任意の補足": "値"},
      "parse_notes": ["不確かな点"]
    }
  ]
}
"""


class WorldObservationNormalizer:
    def __init__(
        self,
        *,
        backend: NormalizerBackend,
        max_retries: int = 1,
        low_confidence_threshold: float = 0.35,
        backend_timeout_sec: float = 45.0,
    ) -> None:
        self.backend = backend
        self.max_retries = max_retries
        self.low_confidence_threshold = low_confidence_threshold
        self.backend_timeout_sec = backend_timeout_sec

    async def normalize(
        self,
        document: WorldObservationRawDocument,
    ) -> WorldObservationNormalizedBatch:
        started_at = time.perf_counter()
        issues: list[WorldObservationParseIssue] = []
        attempts = 0
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            attempts = attempt + 1
            try:
                raw = await asyncio.wait_for(
                    self._run_backend(document),
                    timeout=self.backend_timeout_sec,
                )
                items, parse_issues = parse_normalizer_output(raw)
                issues.extend(parse_issues)
                elapsed_ms = (time.perf_counter() - started_at) * 1000
                return WorldObservationNormalizedBatch(
                    items=tuple(items),
                    trace=WorldObservationNormalizeTrace(
                        model=self.backend.name,
                        elapsed_ms=elapsed_ms,
                        attempts=attempts,
                        issues=tuple(issues),
                    ),
                )
            except Exception as exc:
                last_error = exc
                issues.append(
                    WorldObservationParseIssue(
                        field="normalizer",
                        message=f"{type(exc).__name__}: {exc}",
                    )
                )

        elapsed_ms = (time.perf_counter() - started_at) * 1000
        if last_error is not None:
            issues.append(
                WorldObservationParseIssue(
                    field="normalizer",
                    message="normalizer exhausted retry budget",
                )
            )
        fallback_items = _build_deterministic_fallback_items(document)
        if fallback_items:
            issues.append(
                WorldObservationParseIssue(
                    field="normalizer",
                    message="used deterministic heading fallback after LLM normalizer failure",
                    severity="warning",
                )
            )
            return WorldObservationNormalizedBatch(
                items=tuple(fallback_items),
                trace=WorldObservationNormalizeTrace(
                    model=f"{self.backend.name}:deterministic_fallback",
                    elapsed_ms=elapsed_ms,
                    attempts=attempts,
                    issues=tuple(issues),
                ),
            )
        return WorldObservationNormalizedBatch(
            items=(),
            trace=WorldObservationNormalizeTrace(
                model=self.backend.name,
                elapsed_ms=elapsed_ms,
                attempts=attempts,
                issues=tuple(issues),
            ),
        )

    async def _run_backend(self, document: WorldObservationRawDocument) -> str:
        chunks: list[str] = []
        messages = [{"role": "user", "content": _format_document_for_prompt(document)}]
        stream = chat_stream_with_trace_role(
            self.backend,
            NORMALIZER_SYSTEM_PROMPT,
            messages,
            max_tokens=768,
            trace_role="world_observation_normalizer",
        )
        async for chunk in stream:
            chunks.append(chunk)
        return "".join(chunks)


def parse_normalizer_output(
    raw_text: str,
) -> tuple[list[WorldObservationNormalizedItem], list[WorldObservationParseIssue]]:
    payload = _load_json_object(raw_text)
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("normalizer output must contain items list")

    items: list[WorldObservationNormalizedItem] = []
    issues: list[WorldObservationParseIssue] = []
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            issues.append(
                WorldObservationParseIssue(
                    field=f"items[{index}]",
                    message="item must be an object",
                )
            )
            continue
        item = WorldObservationNormalizedItem.from_json(raw_item)
        item_issues = validate_normalized_item(item, index=index)
        issues.extend(item_issues)
        if not any(issue.severity == "error" for issue in item_issues):
            items.append(item)
    return items, issues


def validate_normalized_item(
    item: WorldObservationNormalizedItem,
    *,
    index: int,
) -> list[WorldObservationParseIssue]:
    issues: list[WorldObservationParseIssue] = []
    for field_name in ("topic", "title", "summary", "source_hint", "raw_excerpt"):
        if not getattr(item, field_name).strip():
            issues.append(
                WorldObservationParseIssue(
                    field=f"items[{index}].{field_name}",
                    message="required field is empty",
                )
            )
    if item.confidence < 0.35:
        issues.append(
            WorldObservationParseIssue(
                field=f"items[{index}].confidence",
                message="low confidence item is saved only as traceable material",
                severity="warning",
            )
        )
    return issues


def _format_document_for_prompt(document: WorldObservationRawDocument) -> str:
    metadata = document.metadata.to_json() if document.metadata else document.raw_frontmatter
    return "\n".join(
        [
            "metadata:",
            json.dumps(metadata, ensure_ascii=False, indent=2),
            "",
            "raw_markdown_body:",
            document.body,
        ]
    )


def _build_deterministic_fallback_items(
    document: WorldObservationRawDocument,
) -> list[WorldObservationNormalizedItem]:
    allowed_topics = {
        "news",
        "economy",
        "technology",
        "culture",
        "local_life",
        "ai",
        "local_inference",
        "other",
    }
    current_topic = "other"
    current_title: str | None = None
    current_lines: list[str] = []
    items: list[WorldObservationNormalizedItem] = []

    def flush() -> None:
        nonlocal current_title, current_lines
        if current_title is None:
            return
        text = _compact_markdown_text("\n".join(current_lines))
        if not text:
            return
        items.append(
            WorldObservationNormalizedItem(
                topic=current_topic,
                title=current_title[:120],
                summary=text[:360],
                source_hint=_extract_source_hint(text),
                freshness="unknown",
                confidence=0.45,
                raw_excerpt=text[:500],
                item_json={"normalizer_fallback": "markdown_heading_excerpt"},
                parse_notes=("LLM normalizer failed; item was derived from Markdown headings.",),
            )
        )
        current_title = None
        current_lines = []

    for line in document.body.splitlines():
        topic_match = _match_topic_heading(line, allowed_topics)
        if topic_match is not None:
            flush()
            current_topic = topic_match
            continue

        title = _match_observation_title(line)
        if title is not None:
            flush()
            current_title = title
            continue

        if current_title is not None:
            current_lines.append(line)

        if len(items) >= 8:
            break

    flush()
    return items[:8]


def _match_topic_heading(line: str, allowed_topics: set[str]) -> str | None:
    stripped = line.strip()
    markdown_match = re.match(r"^##\s+(.+?)\s*$", stripped)
    if markdown_match and not stripped.startswith("###"):
        topic = markdown_match.group(1).strip().lower()
        return topic if topic in allowed_topics else "other"
    lowered = stripped.lower()
    if lowered in allowed_topics:
        return lowered
    return None


def _match_observation_title(line: str) -> str | None:
    stripped = line.strip()
    markdown_match = re.match(r"^###\s+(.+?)\s*$", stripped)
    if markdown_match:
        title = markdown_match.group(1).strip()
    else:
        rendered_match = re.match(
            r"^(?:観測(?:項目)?\s*)?\d+\s*[.)、:：]\s*(.+?)\s*$",
            stripped,
        )
        if rendered_match is None:
            return None
        title = rendered_match.group(1).strip()
    return re.sub(r"^\d+[.)]\s*", "", title).strip()


def _compact_markdown_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    return re.sub(r"\s+", " ", " ".join(line for line in lines if line)).strip()


def _extract_source_hint(text: str) -> str:
    match = re.search(
        r"(出典|source_hint|source|参考)[:：]\s*([^。,\n]{1,120})",
        text,
        re.IGNORECASE,
    )
    if match:
        return match.group(2).strip()
    return "markdown_heading_excerpt"


def _load_json_object(raw_text: str) -> dict[str, Any]:
    stripped = raw_text.strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(stripped[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("normalizer response must be a JSON object")
    return payload
