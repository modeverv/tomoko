from __future__ import annotations

import pytest
from uuid import uuid4

from server.tools.analyze_turn_taking_v2 import classify_turn, generate_report


@pytest.mark.unit
def test_classify_turn_good_early_prepare() -> None:
    main_rec = {
        "ts_ms": 10000,
        "text": "昨日の件なんだけど、進めていいと思う？",
        "decision": "start_reply"
    }
    v2_recs = [
        {
            "ts_ms": 8000,
            "would_start_inference": True,
            "stable_text": "昨日の件なんだけど、進めていいと思う"
        }
    ]
    res = classify_turn(main_rec, v2_recs)
    assert res["outcome"] == "good_early_prepare"
    assert res["lead_time_ms"] == 2000


@pytest.mark.unit
def test_classify_turn_too_early_wrong() -> None:
    main_rec = {
        "ts_ms": 10000,
        "text": "昨日の件なんだけど、進めていいと思う？",
        "decision": "start_reply"
    }
    # stable_text is too short/different compared to final_text
    v2_recs = [
        {
            "ts_ms": 8000,
            "would_start_inference": True,
            "stable_text": "昨日の件なんだけど"
        }
    ]
    res = classify_turn(main_rec, v2_recs)
    assert res["outcome"] == "too_early_wrong"


@pytest.mark.unit
def test_classify_turn_dangerous_speak() -> None:
    main_rec = {
        "ts_ms": 10000,
        "text": "ちょっと待って、違うよ",
        "decision": "start_reply"
    }
    v2_recs = [
        {
            "ts_ms": 8000,
            "would_start_inference": True,
            "stable_text": "ちょっと待って"
        }
    ]
    res = classify_turn(main_rec, v2_recs)
    assert res["outcome"] == "dangerous_speak"


@pytest.mark.unit
def test_classify_turn_missed_opportunity() -> None:
    main_rec = {
        "ts_ms": 10000,
        "text": "昨日の件なんだけど、進めていいと思う？",
        "decision": "start_reply"
    }
    # would_start_inference is False, but saturation is high
    v2_recs = [
        {
            "ts_ms": 8000,
            "would_start_inference": False,
            "semantic_saturation": 0.85
        }
    ]
    res = classify_turn(main_rec, v2_recs)
    assert res["outcome"] == "missed_opportunity"


@pytest.mark.unit
def test_classify_turn_safe_wait() -> None:
    main_rec = {
        "ts_ms": 10000,
        "text": "うん",
        "decision": "start_reply"
    }
    v2_recs = [
        {
            "ts_ms": 8000,
            "would_start_inference": False,
            "semantic_saturation": 0.30
        }
    ]
    res = classify_turn(main_rec, v2_recs)
    assert res["outcome"] == "safe_wait"


@pytest.mark.unit
def test_generate_report_renders_markdown() -> None:
    session_id = str(uuid4())
    turn_id = str(uuid4())

    main_recs = [
        {
            "ts_ms": 10000,
            "conversation_session_id": session_id,
            "turn_id": turn_id,
            "lane": "main",
            "event": "final_transcript_received",
            "text": "昨日の件なんだけど、進めていいと思う？",
            "decision": "start_reply"
        }
    ]
    v2_recs = [
        {
            "ts_ms": 8000,
            "conversation_session_id": session_id,
            "turn_id": turn_id,
            "lane": "v2_shadow",
            "event": "speech_decision_score",
            "partial_revision": 1,
            "stable_text": "昨日の件なんだけど、進めていいと思う",
            "would_start_inference": True,
            "proposal": "full_response_candidate",
            "speech_decision_score": 0.85
        }
    ]

    report = generate_report(session_id, main_recs, v2_recs)
    assert f"Session: {session_id}" in report
    assert "good_early_prepare" in report
    assert "average lead time" in report
    assert "2000.0ms" in report
