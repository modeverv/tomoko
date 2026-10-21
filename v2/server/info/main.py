from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from server.tomoko.db_bridge import (
    insert_world_document_sql,
    insert_world_interpretation_sql,
    insert_world_item_sql,
)


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    starts_at: str
    title: str


@dataclass(frozen=True, slots=True)
class InfoFixtureMaterializationResult:
    documents_upserted: int
    items_upserted: int
    interpretations_upserted: int


def parse_minimal_ics(text: str) -> list[CalendarEvent]:
    events: list[CalendarEvent] = []
    current_start: str | None = None
    current_title: str | None = None
    for line in text.splitlines():
        if line.startswith("DTSTART"):
            current_start = line.split(":", 1)[1].strip()
        elif line.startswith("SUMMARY"):
            current_title = line.split(":", 1)[1].strip()
        elif line.strip() == "END:VEVENT" and current_start and current_title:
            events.append(CalendarEvent(starts_at=current_start, title=current_title))
            current_start = None
            current_title = None
    return events


def calendar_dto_map(events: list[CalendarEvent]) -> dict[str, str]:
    return {event.starts_at: event.title for event in events}


def should_candidate_from_world(
    *,
    confidence: float,
    stale: bool,
    sensitive: bool,
    private: bool,
    do_not_speak: bool,
) -> bool:
    return confidence >= 0.65 and not (stale or sensitive or private or do_not_speak)


async def materialize_info_fixtures_from_env(conn: object) -> InfoFixtureMaterializationResult:
    raw_calendar = _json_env("TOMOKO_V2_FAKE_CALENDAR")
    raw_world = _json_env("TOMOKO_V2_FAKE_WORLD_INFO")
    return await materialize_info_fixtures_from_payloads(
        conn,
        calendar_items=_calendar_items_from_payload(raw_calendar),
        world_items=list(raw_world) if raw_world else [],
    )


async def materialize_info_fixtures_from_payloads(
    conn: object,
    *,
    calendar_items: Mapping[str, str] | None = None,
    world_items: Iterable[Mapping[str, object]] = (),
) -> InfoFixtureMaterializationResult:
    documents = 0
    items = 0
    interpretations = 0
    for starts_at, title in sorted((calendar_items or {}).items()):
        await _upsert_world_fixture(
            conn,
            source="calendar",
            source_key=starts_at,
            raw_text=f"{starts_at} {title}",
            title=title,
            body=f"{starts_at} {title}",
            summary=f"{starts_at} {title}",
            confidence=0.9,
            flags={},
            metadata={"fixture": "calendar"},
        )
        documents += 1
        items += 1
        interpretations += 1
    for item in world_items:
        source_key = str(item.get("source_key", item.get("key", "fake-world")))
        text = str(item.get("text", ""))
        flags = _world_flags(item)
        await _upsert_world_fixture(
            conn,
            source="world",
            source_key=source_key,
            raw_text=text,
            title=source_key,
            body=text,
            summary=text,
            confidence=float(item.get("confidence", 0.0)),
            flags=flags,
            metadata={"fixture": "world"},
        )
        documents += 1
        items += 1
        interpretations += 1
    return InfoFixtureMaterializationResult(
        documents_upserted=documents,
        items_upserted=items,
        interpretations_upserted=interpretations,
    )


async def _upsert_world_fixture(
    conn: object,
    *,
    source: str,
    source_key: str,
    raw_text: str,
    title: str,
    body: str,
    summary: str,
    confidence: float,
    flags: Mapping[str, object],
    metadata: Mapping[str, object],
) -> None:
    document_id = _stable_info_id("document", source, source_key)
    item_id = _stable_info_id("item", source, source_key)
    interpretation_id = _stable_info_id("interpretation", source, source_key)
    trace_id = _stable_info_id("trace", source, source_key)
    for command in [
        insert_world_document_sql(
            document_id=document_id,
            source=source,
            source_key=source_key,
            raw_text=raw_text,
            metadata=metadata,
            trace_id=trace_id,
        ),
        insert_world_item_sql(
            item_id=item_id,
            document_id=document_id,
            title=title,
            body=body,
            confidence=confidence,
            flags=flags,
            trace_id=trace_id,
        ),
        insert_world_interpretation_sql(
            interpretation_id=interpretation_id,
            item_id=item_id,
            summary=summary,
            confidence=confidence,
            flags=flags,
            trace_id=trace_id,
        ),
    ]:
        await conn.execute(command.query, command.params)


def _json_env(name: str) -> object | None:
    raw = os.environ.get(name)
    return json.loads(raw) if raw else None


def _calendar_items_from_payload(payload: object | None) -> dict[str, str]:
    if not payload:
        return {}
    return {str(item["starts_at"]): str(item["title"]) for item in list(payload)}


def _world_flags(item: Mapping[str, object]) -> dict[str, bool]:
    flags = dict(item.get("flags") or {})
    return {
        "stale": bool(item.get("stale", flags.get("stale", False))),
        "sensitive": bool(item.get("sensitive", flags.get("sensitive", False))),
        "private": bool(item.get("private", flags.get("private", False))),
        "do_not_speak": bool(
            item.get("do_not_speak", flags.get("do_not_speak", False))
        ),
    }


def _stable_info_id(kind: str, source: str, source_key: str) -> UUID:
    return uuid5(uuid5(NAMESPACE_URL, "tomoko-v2-info"), f"{kind}:{source}:{source_key}")
