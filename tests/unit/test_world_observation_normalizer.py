from __future__ import annotations

import asyncio

import pytest

from server.world_observations.normalizer import (
    WorldObservationNormalizer,
    parse_normalizer_output,
)
from server.world_observations.raw_markdown import parse_raw_markdown

pytestmark = pytest.mark.unit


class FakeBackend:
    name = "fake-normalizer"

    def __init__(self, text: str) -> None:
        self.text = text
        self.chat_stream_calls = 0

    async def chat_stream(self, system_prompt: str, messages: list[dict[str, str]]):
        self.chat_stream_calls += 1
        assert "background normalizer" in system_prompt
        assert messages
        yield self.text


class EmptyStructuredBackend(FakeBackend):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.chat_stream_structured_calls = 0

    async def chat_stream_structured(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        *,
        json_schema: dict,
        max_tokens: int,
        trace_role: str,
    ):
        self.chat_stream_structured_calls += 1
        assert "background normalizer" in system_prompt
        assert messages
        assert json_schema["name"] == "world_observation_normalized_batch"
        assert max_tokens == 4096
        assert trace_role == "world_observation_normalizer"
        if False:
            yield ""


class SlowBackend(FakeBackend):
    async def chat_stream(self, system_prompt: str, messages: list[dict[str, str]]):
        del system_prompt, messages
        await asyncio.sleep(1)
        yield self.text


def test_parse_normalizer_output_rejects_missing_required_field() -> None:
    items, issues = parse_normalizer_output(
        '{"items":[{"topic":"ai","title":"","summary":"s","source_hint":"x",'
        '"freshness":"fresh","confidence":0.8,"raw_excerpt":"e"}]}'
    )

    assert items == []
    assert issues[0].field == "items[0].title"


@pytest.mark.unit
async def test_normalizer_returns_trace_and_low_confidence_warning() -> None:
    document = parse_raw_markdown(
        """\
---
schema_version: 1
kind: world_observation_batch
generated_by: sample
observed_at: 2026-05-25T09:00:00+09:00
language: ja
topics: [ai]
source_policy: public_web_summary_only
collection_prompt_version: daily_world_observation_v1
---
本文。
"""
    )
    normalizer = WorldObservationNormalizer(
        backend=FakeBackend(
            '{"items":[{"topic":"ai","title":"小型モデル","summary":"端末内推論",'
            '"source_hint":"sample","freshness":"fresh","confidence":0.3,'
            '"raw_excerpt":"端末内推論"}]}'
        )
    )

    batch = await normalizer.normalize(document)

    assert len(batch.items) == 1
    assert batch.trace.model == "fake-normalizer"
    assert any(issue.severity == "warning" for issue in batch.trace.issues)


async def test_normalizer_uses_plain_chat_even_when_structured_stream_exists() -> None:
    document = parse_raw_markdown(
        """\
---
schema_version: 1
kind: world_observation_batch
generated_by: sample
observed_at: 2026-05-30T09:00:00+09:00
language: ja
topics: [ai]
source_policy: public_web_summary_only
collection_prompt_version: daily_world_observation_v1
---
本文。
"""
    )
    backend = EmptyStructuredBackend(
        '{"items":[{"topic":"ai","title":"構造化出力","summary":"空返答時に通常 chat へ戻す",'
        '"source_hint":"sample","freshness":"fresh","confidence":0.8,'
        '"raw_excerpt":"本文。","item_json":{},"parse_notes":[]}]}'
    )
    normalizer = WorldObservationNormalizer(backend=backend)

    batch = await normalizer.normalize(document)

    assert len(batch.items) == 1
    assert batch.items[0].title == "構造化出力"
    assert backend.chat_stream_calls == 1
    assert backend.chat_stream_structured_calls == 0


async def test_normalizer_uses_heading_fallback_after_llm_parse_failure() -> None:
    document = parse_raw_markdown(
        """\
---
schema_version: 1
kind: world_observation_batch
generated_by: sample
observed_at: 2026-05-30T09:00:00+09:00
language: ja
topics: [news, ai]
source_policy: public_web_summary_only
collection_prompt_version: daily_world_observation_v1
---
# 外界観測レポート

## news

### 1. 見出しA

事実: 公開情報だけから作った観測。

## ai

### 2. 見出しB

事実: ローカル推論の更新。
"""
    )
    normalizer = WorldObservationNormalizer(
        backend=FakeBackend("not json"),
        max_retries=0,
    )

    batch = await normalizer.normalize(document)

    assert [item.title for item in batch.items] == ["見出しA", "見出しB"]
    assert batch.items[0].topic == "news"
    assert batch.trace.model == "fake-normalizer:deterministic_fallback"
    assert any(issue.severity == "warning" for issue in batch.trace.issues)


async def test_normalizer_timeout_uses_heading_fallback() -> None:
    document = parse_raw_markdown(
        """\
---
schema_version: 1
kind: world_observation_batch
generated_by: sample
observed_at: 2026-05-30T09:00:00+09:00
language: ja
topics: [news]
source_policy: public_web_summary_only
collection_prompt_version: daily_world_observation_v1
---
## news

### 1. タイムアウト後の見出し

事実: fallback で拾う。
"""
    )
    normalizer = WorldObservationNormalizer(
        backend=SlowBackend('{"items":[]}'),
        max_retries=0,
        backend_timeout_sec=0.01,
    )

    batch = await normalizer.normalize(document)

    assert [item.title for item in batch.items] == ["タイムアウト後の見出し"]
    assert any("TimeoutError" in issue.message for issue in batch.trace.issues)
