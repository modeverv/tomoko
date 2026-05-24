from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from server.shared.candidate import ThinkerSourceContext
from server.shared.diary import InMemoryDiaryStore
from server.thinker.sources.diary import DiarySource


@pytest.mark.unit
async def test_diary_source_prefers_yesterday_and_uses_diary_dedupe() -> None:
    store = InMemoryDiaryStore()
    today = await store.insert_entry(
        diary_date=date(2026, 5, 24),
        body_text="今日はまだ書きかけ。",
        created_at=datetime(2026, 5, 24, 23, 0, tzinfo=UTC),
    )
    yesterday = await store.insert_entry(
        diary_date=date(2026, 5, 23),
        body_text="昨日は静かだった。少しだけ話したいことが残った。",
        created_at=datetime(2026, 5, 23, 23, 0, tzinfo=UTC),
    )
    source = DiarySource(diary_store=store)

    seeds = await source.collect(
        ThinkerSourceContext(
            observed_at=datetime(2026, 5, 24, 9, 0, tzinfo=UTC),
        )
    )

    assert today.id != yesterday.id
    assert len(seeds) == 1
    assert seeds[0].source == "diary"
    assert seeds[0].dedupe_key == f"diary:{yesterday.id}"
    assert seeds[0].context_tags == (
        f"dedupe:diary:{yesterday.id}",
        "diary_date:2026-05-23",
    )
    assert "昨日は静かだった。" in seeds[0].seed_text
    assert "少しだけ話したいこと" not in seeds[0].seed_text


@pytest.mark.unit
async def test_diary_source_returns_empty_without_entries() -> None:
    source = DiarySource(diary_store=InMemoryDiaryStore())

    seeds = await source.collect(
        ThinkerSourceContext(
            observed_at=datetime(2026, 5, 24, 9, 0, tzinfo=UTC),
        )
    )

    assert seeds == []
