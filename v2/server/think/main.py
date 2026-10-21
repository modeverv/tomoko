from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from server.info.main import should_candidate_from_world
from server.shared.models import (
    CandidateLifecycle,
    CandidateRecord,
    CandidateSeed,
    SessionSummary,
)
from server.tomoko.db_bridge import (
    insert_candidate_sql,
    load_recent_session_summaries,
    load_recent_world_interpretation_rows,
)


def score_seed(seed: CandidateSeed) -> float:
    return _clamp(
        seed.priority * 0.4
        + seed.urgency * 0.3
        + seed.maturity * 0.2
        - seed.intrusion * 0.1
    )


@dataclass(slots=True)
class CandidateStore:
    records: dict[tuple[str, str], CandidateRecord] = field(default_factory=dict)

    def upsert_seed(self, seed: CandidateSeed) -> CandidateRecord:
        key = (seed.source, seed.source_key)
        existing = self.records.get(key)
        if existing and existing.lifecycle == CandidateLifecycle.ACTIVE:
            return existing
        record = CandidateRecord(
            seed_id=seed.id,
            source=seed.source,
            source_key=seed.source_key,
            text=seed.text,
            priority=seed.priority,
            urgency=seed.urgency,
            intrusion=seed.intrusion,
            maturity=seed.maturity,
            lifecycle=CandidateLifecycle.ACTIVE,
            context_tags=seed.context_tags,
            candidate_score=score_seed(seed),
            trace_id=seed.trace_id,
        )
        self.records[key] = record
        return record

    def active(self) -> list[CandidateRecord]:
        return [
            record
            for record in self.records.values()
            if record.lifecycle == CandidateLifecycle.ACTIVE
        ]


@dataclass(frozen=True, slots=True)
class CandidateMaterializationResult:
    summaries_read: int
    world_items_read: int
    candidates_upserted: int


def calendar_reminder_seed(starts_at: str, title: str) -> CandidateSeed:
    return CandidateSeed(
        source="calendar",
        source_key=starts_at,
        text=f"{starts_at} {title}",
        priority=0.8,
        urgency=0.7,
        intrusion=0.2,
        maturity=1.0,
        context_tags=("calendar", "reminder"),
    )


def calendar_item_seeds(calendar_items: Mapping[str, str]) -> list[CandidateSeed]:
    return [
        calendar_reminder_seed(starts_at, title)
        for starts_at, title in sorted(calendar_items.items())
    ]


def summary_memory_seed(summary: SessionSummary) -> CandidateSeed:
    return CandidateSeed(
        source="summary",
        source_key=str(summary.id),
        text=f"{summary.keyword}: {summary.conclusion}",
        priority=0.55,
        urgency=0.25,
        intrusion=0.15,
        maturity=0.8,
        context_tags=("summary", "memory", f"keyword:{summary.keyword}"),
        trace_id=summary.trace_id,
    )


def world_info_seed(
    *,
    source_key: str,
    text: str,
    confidence: float,
    stale: bool,
    sensitive: bool,
    private: bool,
    do_not_speak: bool,
) -> CandidateSeed | None:
    if not should_candidate_from_world(
        confidence=confidence,
        stale=stale,
        sensitive=sensitive,
        private=private,
        do_not_speak=do_not_speak,
    ):
        return None
    return CandidateSeed(
        source="world",
        source_key=source_key,
        text=text,
        priority=_clamp(0.35 + confidence * 0.45),
        urgency=_clamp(0.25 + confidence * 0.35),
        intrusion=0.25,
        maturity=_clamp(confidence),
        context_tags=("world", "info"),
    )


def world_seed_from_interpretation_row(row: Mapping[str, object]) -> CandidateSeed | None:
    flags = dict(row.get("flags") or {})
    return world_info_seed(
        source_key=str(row["id"]),
        text=str(row["summary"]),
        confidence=float(row["confidence"]),
        stale=bool(flags.get("stale", False)),
        sensitive=bool(flags.get("sensitive", False)),
        private=bool(flags.get("private", False)),
        do_not_speak=bool(flags.get("do_not_speak", False)),
    )


def build_candidates(
    *,
    calendar_items: Mapping[str, str] | None = None,
    summaries: Iterable[SessionSummary] = (),
    world_seeds: Iterable[CandidateSeed | None] = (),
) -> list[CandidateRecord]:
    store = CandidateStore()
    for seed in calendar_item_seeds(calendar_items or {}):
        store.upsert_seed(seed)
    for summary in summaries:
        store.upsert_seed(summary_memory_seed(summary))
    for seed in world_seeds:
        if seed is not None:
            store.upsert_seed(seed)
    return store.active()


async def materialize_candidates_from_db(
    conn: object,
    *,
    summary_limit: int = 8,
    world_limit: int = 8,
) -> CandidateMaterializationResult:
    summaries = await load_recent_session_summaries(conn, limit=summary_limit)
    world_rows = await load_recent_world_interpretation_rows(conn, limit=world_limit)
    records = build_candidates(
        summaries=summaries,
        world_seeds=[world_seed_from_interpretation_row(row) for row in world_rows],
    )
    for record in records:
        command = insert_candidate_sql(record)
        await conn.execute(command.query, command.params)
    return CandidateMaterializationResult(
        summaries_read=len(summaries),
        world_items_read=len(world_rows),
        candidates_upserted=len(records),
    )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
