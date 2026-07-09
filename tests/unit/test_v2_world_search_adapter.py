from __future__ import annotations

import pytest

from scripts.world_search_perplexity import items_from_research_result

pytestmark = pytest.mark.unit


def test_items_from_completed_research_result_maps_answer_and_citations() -> None:
    payload = {
        "status": "completed",
        "query": "明日の東京の天気",
        "short_answer": "明日の東京は、雲が広がって少し雨が降る見込みです。",
        "bullets": ["朝は曇り", ""],
        "citations": [
            {"title": "AccuWeather", "url": "https://x", "source": "www.accuweather.com"},
        ],
        "confidence": 0.7,
        "provider_trace_id": "perplexity-20260704T073600Z",
    }

    items = items_from_research_result(payload)

    assert len(items) == 2
    assert items[0]["source_key"] == "perplexity-20260704T073600Z-answer"
    assert "少し雨が降る見込み" in items[0]["text"]
    assert "www.accuweather.com" in items[0]["text"]
    assert items[0]["confidence"] == 0.7
    assert items[1]["text"] == "朝は曇り"


def test_items_from_failed_research_result_is_empty() -> None:
    payload = {
        "status": "failed",
        "query": "明日の大阪の天気",
        "short_answer": "",
        "error_reason": "answer did not settle",
    }
    assert items_from_research_result(payload) == []
