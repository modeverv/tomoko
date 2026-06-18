from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from server.shared.models import (
    WorldObservationParseIssue,
    WorldObservationRawDocument,
    WorldObservationRawMetadata,
)

RAW_SCHEMA_VERSION = 1
RAW_KIND = "world_observation_batch"

_REQUIRED_FIELDS = (
    "schema_version",
    "kind",
    "generated_by",
    "observed_at",
    "language",
    "topics",
    "source_policy",
    "collection_prompt_version",
)


def read_raw_markdown(path: str | Path) -> WorldObservationRawDocument:
    file_path = Path(path)
    text = file_path.read_text()
    return parse_raw_markdown(text, path=str(file_path))


def parse_raw_markdown(text: str, *, path: str = "<memory>") -> WorldObservationRawDocument:
    frontmatter, body, issues = _split_frontmatter(text)
    if frontmatter is None:
        return WorldObservationRawDocument(
            path=path,
            metadata=None,
            body=body,
            raw_frontmatter={},
            issues=tuple(issues),
        )

    raw_metadata = _parse_frontmatter(frontmatter)
    issues.extend(_validate_raw_metadata(raw_metadata))
    metadata = _build_metadata(raw_metadata, issues)
    return WorldObservationRawDocument(
        path=path,
        metadata=metadata,
        body=body,
        raw_frontmatter=raw_metadata,
        issues=tuple(issues),
    )


def _split_frontmatter(
    text: str,
) -> tuple[str | None, str, list[WorldObservationParseIssue]]:
    if not text.startswith("---\n"):
        return (
            None,
            text,
            [
                WorldObservationParseIssue(
                    field="frontmatter",
                    message="missing opening frontmatter delimiter",
                )
            ],
        )

    end = text.find("\n---", 4)
    if end < 0:
        return (
            None,
            text,
            [
                WorldObservationParseIssue(
                    field="frontmatter",
                    message="missing closing frontmatter delimiter",
                )
            ],
        )

    frontmatter = text[4:end]
    body_start = end + len("\n---")
    if body_start < len(text) and text[body_start] == "\n":
        body_start += 1
    return frontmatter, text[body_start:], []


def _parse_frontmatter(frontmatter: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_line in frontmatter.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and current_list_key is not None:
            payload.setdefault(current_list_key, []).append(stripped[2:].strip())
            continue
        current_list_key = None
        if ":" not in stripped:
            payload.setdefault("_unparsed", []).append(stripped)
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        parsed_value = _parse_scalar(value.strip())
        payload[key] = parsed_value
        if parsed_value == []:
            current_list_key = key
    return payload


def _parse_scalar(value: str) -> Any:
    if value == "":
        return []
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_strip_quotes(part.strip()) for part in inner.split(",")]
    if value.isdigit():
        return int(value)
    return _strip_quotes(value)


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _validate_raw_metadata(
    payload: dict[str, Any],
) -> list[WorldObservationParseIssue]:
    issues: list[WorldObservationParseIssue] = []
    for field in _REQUIRED_FIELDS:
        if field not in payload or payload[field] in (None, "", []):
            issues.append(
                WorldObservationParseIssue(
                    field=field,
                    message="required metadata field is missing",
                )
            )

    if payload.get("schema_version") not in (RAW_SCHEMA_VERSION, str(RAW_SCHEMA_VERSION)):
        issues.append(
            WorldObservationParseIssue(
                field="schema_version",
                message=f"schema_version must be {RAW_SCHEMA_VERSION}",
            )
        )
    if payload.get("kind") not in (None, "", RAW_KIND):
        issues.append(
            WorldObservationParseIssue(
                field="kind",
                message=f"kind must be {RAW_KIND}",
            )
        )
    if "observed_at" in payload:
        try:
            _parse_datetime(payload["observed_at"])
        except ValueError:
            issues.append(
                WorldObservationParseIssue(
                    field="observed_at",
                    message="observed_at must be ISO-8601 datetime",
                )
            )
    if "topics" in payload and not _as_topics(payload["topics"]):
        issues.append(
            WorldObservationParseIssue(
                field="topics",
                message="topics must contain at least one topic",
            )
        )
    if payload.get("_unparsed"):
        issues.append(
            WorldObservationParseIssue(
                field="frontmatter",
                message="frontmatter contains unparsed lines",
                severity="warning",
            )
        )
    return issues


def _build_metadata(
    payload: dict[str, Any],
    issues: list[WorldObservationParseIssue],
) -> WorldObservationRawMetadata | None:
    if any(issue.severity == "error" for issue in issues):
        return None
    return WorldObservationRawMetadata(
        schema_version=int(payload["schema_version"]),
        kind=str(payload["kind"]),
        generated_by=str(payload["generated_by"]),
        observed_at=_parse_datetime(payload["observed_at"]),
        language=str(payload["language"]),
        topics=tuple(_as_topics(payload["topics"])),
        source_policy=str(payload["source_policy"]),
        collection_prompt_version=str(payload["collection_prompt_version"]),
    )


def _parse_datetime(value: object) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    return datetime.fromisoformat(text)


def _as_topics(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [
        item.strip()
        for item in str(value).replace("，", ",").split(",")
        if item.strip()
    ]
