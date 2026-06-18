from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.world_observations.operator_client import (
    WORLD_OBSERVE_TOOL_NAME,
    WorldObservationMcpClient,
    WorldObservationOperatorRequest,
    WorldObservationOperatorResult,
    build_daily_world_observation_request,
    create_default_world_observation_mcp_client,
    parse_world_observation_mcp_response,
    save_world_observation_markdown,
)
from server.world_observations.raw_markdown import read_raw_markdown


@pytest.mark.unit
async def test_world_observation_mcp_client_builds_json_rpc_tool_call() -> None:
    calls: list[tuple[list[str], str, float, Path | None]] = []

    async def fake_runner(
        command: list[str],
        stdin_text: str,
        timeout_sec: float,
        cwd: Path | None,
    ) -> str:
        calls.append((command, stdin_text, timeout_sec, cwd))
        return json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "structuredContent": {
                        "status": "completed",
                        "title": "world_observation_2026-06-05",
                        "observed_at": "2026-06-05T09:00:00+09:00",
                        "markdown_text": "# 外界観測\n本文です。",
                        "provider_trace_id": "world-observation-test",
                    },
                    "isError": False,
                },
            },
            ensure_ascii=False,
        )

    client = WorldObservationMcpClient(
        command=("uv", "run", "tomoko-research-mcp"),
        runner=fake_runner,
    )
    result = await client.observe(
        WorldObservationOperatorRequest(
            prompt="公開情報だけでまとめて",
            title="world_observation_2026-06-05",
            observed_at="2026-06-05T09:00:00+09:00",
        )
    )

    assert result.is_completed()
    assert result.markdown_text == "# 外界観測\n本文です。"
    request_payload = json.loads(calls[0][1])
    assert request_payload["method"] == "tools/call"
    assert request_payload["params"]["name"] == WORLD_OBSERVE_TOOL_NAME
    assert request_payload["params"]["arguments"]["title"] == "world_observation_2026-06-05"


@pytest.mark.unit
def test_parse_world_observation_mcp_response_maps_error() -> None:
    result = parse_world_observation_mcp_response(
        json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"message": "bad"}}),
        fallback_title="world_observation_2026-06-05",
        fallback_observed_at="2026-06-05T09:00:00+09:00",
    )

    assert result.status == "failed"
    assert result.error_reason == "bad"


@pytest.mark.unit
def test_build_daily_world_observation_request_rewrites_template_date() -> None:
    request = build_daily_world_observation_request(
        prompt_template=(
            "title: `world_observation_2026-05-25`\n"
            "observed_at: 2026-05-25T09:00:00+09:00\n"
            "# 外界観測レポート 2026-05-25\n"
        ),
        collection_date="2026-06-05",
    )

    assert request.title == "world_observation_2026-06-05"
    assert request.observed_at == "2026-06-05T09:00:00+09:00"
    assert "world_observation_2026-06-05" in request.prompt
    assert "2026-05-25" not in request.prompt


@pytest.mark.unit
def test_save_world_observation_markdown_adds_valid_frontmatter(tmp_path: Path) -> None:
    result = WorldObservationOperatorResult(
        status="completed",
        title="world_observation_2026-06-05",
        observed_at="2026-06-05T09:00:00+09:00",
        markdown_text=(
            "---\n"
            "old: frontmatter\n"
            "---\n"
            "# 外界観測レポート 2026-06-05\n\n"
            "## news\n\n"
            "### 観測1\n"
            "事実: 公開情報で確認できる主要ニュースです。\n"
            "推測・含意: 生活と開発環境への影響は限定的ですが、継続確認が必要です。\n"
            "source_hint: Reuters / 公式発表\n\n"
            "## economy\n\n"
            "事実: 経済指標の公開情報です。\n"
            "推測・含意: 家計や開発投資の背景になります。\n"
            "source_hint: 官公庁\n\n"
            "## technology\n\n"
            "事実: 技術企業の公開情報です。\n"
            "推測・含意: 開発環境の選択材料になります。\n"
            "source_hint: 企業公式ブログ\n\n"
            "## culture\n\n"
            "事実: 文化イベントの公開情報です。\n"
            "推測・含意: 雑談の入口になります。\n"
            "source_hint: 公式サイト\n\n"
            "## local_life\n\n"
            "事実: 地域生活に関わる公開情報です。\n"
            "推測・含意: 今日の予定調整に影響します。\n"
            "source_hint: 自治体発表\n\n"
            "## ai\n\n"
            "事実: AI 関連の公開情報です。\n"
            "推測・含意: ローカル推論構成の検討材料になります。\n"
            "source_hint: arXiv / 企業公式ブログ\n\n"
            "## local_inference\n\n"
            "事実: ローカル推論ランタイムの公開情報です。\n"
            "推測・含意: Tomoko の背景知識として使えます。\n"
            "source_hint: GitHub / release notes\n\n"
            + "本文です。\n" * 180
        ),
    )

    path = save_world_observation_markdown(
        result,
        output_dir=tmp_path,
        collection_date="2026-06-05",
    )
    document = read_raw_markdown(path)

    assert path.name == "2026-06-05-world-observation.md"
    assert document.is_valid
    assert document.metadata is not None
    assert document.metadata.generated_by == "perplexity"
    assert document.body.startswith("# 外界観測レポート 2026-06-05\n\n## news")


@pytest.mark.unit
def test_save_world_observation_markdown_accepts_rendered_inner_text(
    tmp_path: Path,
) -> None:
    rendered_body = (
        "schema_version: 1\n"
        "kind: world_observation_batch\n"
        "generated_by: perplexity\n"
        "observed_at: 2026-06-05T09:00:00+09:00\n"
        "language: ja\n"
        "topics: [news, economy, technology, culture, local_life, ai, local_inference]\n"
        "source_policy: public_web_summary_only\n"
        "collection_prompt_version: daily_world_observation_v1\n\n"
        "外界観測レポート 2026-06-05\n\n"
        "news\n"
        "観測項目 1：国内外の主要ニュース\n"
        "事実： 公開情報で確認できる主要ニュースです。\n"
        "推測・含意： 日常会話で触れるなら短い背景説明に留めるのがよさそうです。\n"
        "source_hint： Reuters / 公式発表\n\n"
        "economy\n"
        "事実: 市場と政策の公開情報です。\n"
        "推測・含意: 生活コストの話題につながります。\n"
        "source_hint: Bloomberg / 官公庁\n\n"
        "technology\n"
        "事実: 技術企業と研究機関の公開情報です。\n"
        "推測・含意: 開発環境の選択に影響します。\n"
        "source_hint: 企業公式ブログ\n\n"
        "culture\n"
        "事実: 文化イベントの公開情報です。\n"
        "推測・含意: 雑談の入口になります。\n"
        "source_hint: 公式サイト\n\n"
        "local_life\n"
        "事実: 地域生活に関わる公開情報です。\n"
        "推測・含意: 今日の予定調整に影響します。\n"
        "source_hint: 自治体発表\n\n"
        "ai\n"
        "事実: AI 関連の公開情報です。\n"
        "推測・含意: ローカル推論構成の検討材料になります。\n"
        "source_hint: arXiv / 企業公式ブログ\n\n"
        "local_inference\n"
        "事実: ローカル推論ランタイムの公開情報です。\n"
        "推測・含意: Tomoko の背景知識として使えます。\n"
        "source_hint: GitHub / release notes\n\n"
        + "本文です。\n" * 140
    )
    result = WorldObservationOperatorResult(
        status="completed",
        title="world_observation_2026-06-05",
        observed_at="2026-06-05T09:00:00+09:00",
        markdown_text=rendered_body,
    )

    path = save_world_observation_markdown(
        result,
        output_dir=tmp_path,
        collection_date="2026-06-05",
    )
    document = read_raw_markdown(path)

    assert document.is_valid
    assert document.body.startswith("外界観測レポート 2026-06-05")
    assert "schema_version:" not in document.body


@pytest.mark.unit
def test_save_world_observation_markdown_rejects_provider_document_summary(
    tmp_path: Path,
) -> None:
    result = WorldObservationOperatorResult(
        status="completed",
        title="world_observation_2026-06-05",
        observed_at="2026-06-05T09:00:00+09:00",
        markdown_text=(
            "world_observation_2026-06-05.md を作成・共有しました。\n"
            "構成の概要は以下のとおりです。\n"
            "各 topic の内容サマリー\n"
        ),
    )

    with pytest.raises(ValueError, match="too short|provider document summary"):
        save_world_observation_markdown(
            result,
            output_dir=tmp_path,
            collection_date="2026-06-05",
        )


@pytest.mark.unit
def test_default_world_observation_client_points_to_sibling_operator(monkeypatch) -> None:
    monkeypatch.delenv("TOMOKO_WORLD_OBSERVATION_MCP_COMMAND", raising=False)

    client = create_default_world_observation_mcp_client()

    assert client.command == ("uv", "run", "tomoko-research-mcp")
    assert client.cwd is not None
    assert client.cwd.name == "tomoko-research-operator"
    assert client.cwd.parent.name == "by-llms"
