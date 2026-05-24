from __future__ import annotations

from datetime import timedelta

from server.shared.candidate import CandidateSeed, ThinkerSourceContext
from server.shared.diary import DiaryEntry, DiaryStore

_SOURCE_NAME = "diary"


class DiarySource:
    def __init__(
        self,
        *,
        diary_store: DiaryStore,
        priority: float = 0.65,
        ttl: timedelta = timedelta(hours=8),
    ) -> None:
        self.diary_store = diary_store
        self.priority = priority
        self.ttl = ttl

    async def collect(self, context: ThinkerSourceContext) -> list[CandidateSeed]:
        entries = await self.diary_store.fetch_recent_entries(limit=3)
        if not entries:
            return []
        entry = _prefer_yesterday(entries, context) or entries[0]
        prompt_seed = _seed_text_from_entry(entry)
        if prompt_seed is None:
            return []
        return [
            CandidateSeed(
                seed_text=prompt_seed,
                source=_SOURCE_NAME,
                priority=self.priority,
                expires_at=context.observed_at + self.ttl,
                dedupe_key=f"{_SOURCE_NAME}:{entry.id}",
                context_tags=(f"diary_date:{entry.diary_date.isoformat()}",),
            )
        ]


def _prefer_yesterday(
    entries: list[DiaryEntry],
    context: ThinkerSourceContext,
) -> DiaryEntry | None:
    yesterday = context.observed_at.date() - timedelta(days=1)
    for entry in entries:
        if entry.diary_date == yesterday:
            return entry
    return None


def _seed_text_from_entry(entry: DiaryEntry) -> str | None:
    body = " ".join(line.strip() for line in entry.body_text.splitlines() if line.strip())
    if not body:
        return None
    first_sentence = _first_sentence(body)
    return f"昨日の日記に書いたことから、短く自然に話しかける: {first_sentence}"


def _first_sentence(text: str) -> str:
    for delimiter in ("。", "！", "？"):
        index = text.find(delimiter)
        if index >= 0:
            return text[: index + 1]
    return text[:80]
