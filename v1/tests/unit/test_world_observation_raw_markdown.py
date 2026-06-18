from __future__ import annotations

import pytest

from server.world_observations.raw_markdown import parse_raw_markdown

pytestmark = pytest.mark.unit

VALID_MARKDOWN = """\
---
schema_version: 1
kind: world_observation_batch
generated_by: sample
observed_at: 2026-05-25T09:00:00+09:00
language: ja
topics: [news, ai]
source_policy: public_web_summary_only
collection_prompt_version: daily_world_observation_v1
---
# body

本文はそのまま保持する。
"""


def test_raw_markdown_parser_preserves_body_and_metadata() -> None:
    document = parse_raw_markdown(VALID_MARKDOWN, path="sample.md")

    assert document.is_valid
    assert document.metadata is not None
    assert document.metadata.kind == "world_observation_batch"
    assert document.metadata.topics == ("news", "ai")
    assert document.body == "# body\n\n本文はそのまま保持する。\n"


def test_raw_markdown_parser_reports_issues_without_changing_body() -> None:
    document = parse_raw_markdown(
        """\
---
schema_version: 1
kind: world_observation_batch
generated_by: sample
language: ja
topics: []
source_policy: public_web_summary_only
collection_prompt_version: daily_world_observation_v1
---
本文。
"""
    )

    assert not document.is_valid
    assert document.body == "本文。\n"
    assert {issue.field for issue in document.issues} >= {"observed_at", "topics"}
