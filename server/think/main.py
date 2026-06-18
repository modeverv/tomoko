from __future__ import annotations

from dataclasses import dataclass, field

from server.shared.models import CandidateLifecycle, CandidateRecord, CandidateSeed


def score_seed(seed: CandidateSeed) -> float:
    return seed.priority * 0.4 + seed.urgency * 0.3 + seed.maturity * 0.2 - seed.intrusion * 0.1


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
