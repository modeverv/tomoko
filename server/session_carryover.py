from __future__ import annotations

import hashlib
from dataclasses import dataclass

from server.shared.models import MemoryHit

RETRIEVED_CONTEXT_CARRYOVER_MAX_ENTRIES = 6
RETRIEVED_CONTEXT_CARRYOVER_MAX_CHARS = 900


@dataclass
class RetrievedContextCarryoverEntry:
    key: str
    memory: MemoryHit
    added_seq: int
    last_used_seq: int


@dataclass(frozen=True)
class CarryoverEviction:
    reason: str
    key: str
    similarity: float
    chars: int


@dataclass(frozen=True)
class CarryoverMergeResult:
    memories: list[MemoryHit]
    carried_count: int
    fresh_count: int
    merged_count: int


@dataclass(frozen=True)
class CarryoverRememberResult:
    added: int
    total: int
    evicted: tuple[CarryoverEviction, ...]


class RetrievedContextCarryoverState:
    def __init__(self) -> None:
        self.entries: list[RetrievedContextCarryoverEntry] = []
        self.sequence = 0

    def merge_carried_long_term_memory(
        self,
        fresh_memory: list[MemoryHit],
    ) -> CarryoverMergeResult:
        carried = self.carried_long_term_memory()
        if not carried:
            return CarryoverMergeResult(
                memories=fresh_memory,
                carried_count=0,
                fresh_count=len(fresh_memory),
                merged_count=len(fresh_memory),
            )

        merged: list[MemoryHit] = []
        seen: set[str] = set()
        for hit in [*fresh_memory, *carried]:
            key = retrieved_context_key(hit)
            if key in seen:
                continue
            seen.add(key)
            merged.append(hit)

        return CarryoverMergeResult(
            memories=merged,
            carried_count=len(carried),
            fresh_count=len(fresh_memory),
            merged_count=len(merged),
        )

    def carried_long_term_memory(self) -> list[MemoryHit]:
        if not self.entries:
            return []
        self.sequence += 1
        used_seq = self.sequence
        for entry in self.entries:
            entry.last_used_seq = used_seq
        return [entry.memory for entry in self.entries]

    def remember(self, memories: list[MemoryHit]) -> CarryoverRememberResult | None:
        if not memories:
            return None
        existing_by_key = {entry.key: entry for entry in self.entries}
        added = 0
        for memory in memories:
            key = retrieved_context_key(memory)
            if key in existing_by_key:
                existing_by_key[key].memory = memory
                existing_by_key[key].last_used_seq = self.sequence
                continue
            self.sequence += 1
            entry = RetrievedContextCarryoverEntry(
                key=key,
                memory=memory,
                added_seq=self.sequence,
                last_used_seq=self.sequence,
            )
            self.entries.append(entry)
            existing_by_key[key] = entry
            added += 1

        evicted = self.evict()
        return CarryoverRememberResult(
            added=added,
            total=len(self.entries),
            evicted=tuple(evicted),
        )

    def evict(self) -> list[CarryoverEviction]:
        evicted: list[CarryoverEviction] = []

        def total_chars() -> int:
            return sum(len(entry.memory.text) for entry in self.entries)

        while len(self.entries) > RETRIEVED_CONTEXT_CARRYOVER_MAX_ENTRIES:
            evicted.append(self.evict_one(reason="entry_count"))
        while self.entries and total_chars() > RETRIEVED_CONTEXT_CARRYOVER_MAX_CHARS:
            evicted.append(self.evict_one(reason="text_budget"))
        return evicted

    def evict_one(self, *, reason: str) -> CarryoverEviction:
        victim = min(
            self.entries,
            key=lambda entry: (
                entry.last_used_seq,
                entry.memory.similarity,
                entry.added_seq,
            ),
        )
        self.entries.remove(victim)
        return CarryoverEviction(
            reason=reason,
            key=victim.key,
            similarity=victim.memory.similarity,
            chars=len(victim.memory.text),
        )

    def clear(self) -> int:
        count = len(self.entries)
        self.entries.clear()
        return count


def retrieved_context_key(hit: MemoryHit) -> str:
    if hit.source_id:
        return hit.source_id
    normalized_text = " ".join(hit.text.split())
    digest = hashlib.sha1(normalized_text.encode("utf-8")).hexdigest()[:16]
    return f"{hit.speaker}:{hit.timestamp.isoformat()}:{digest}"
