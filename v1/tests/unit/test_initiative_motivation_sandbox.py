from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from server.tools.export_initiative_candidates import (
    _arrival_row_to_json,
    _utterance_row_to_json,
)
from server.tools.initiative_motivation_sandbox import (
    CandidateView,
    build_prompt_preview,
    candidate_lifecycle,
    recent_session_groups,
    render_html,
    simulate_from_logs,
    simulate_recent_sessions_from_logs,
    simulate_silence,
)
from server.tools.simulate_initiative_motivation import _conversation_row_to_record


@pytest.mark.unit
def test_candidate_lifecycle_classifies_dead_candidates() -> None:
    now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    base = {
        "expires_at": (now + timedelta(minutes=1)).isoformat(),
        "spoken_at": None,
        "dismissed_at": None,
    }

    assert candidate_lifecycle(base, now) == "active"
    assert candidate_lifecycle(base | {"spoken_at": now.isoformat()}, now) == "spoken"
    assert (
        candidate_lifecycle(base | {"dismissed_at": now.isoformat()}, now)
        == "dismissed"
    )
    assert (
        candidate_lifecycle(
            base | {"expires_at": (now - timedelta(seconds=1)).isoformat()},
            now,
        )
        == "expired"
    )


@pytest.mark.unit
def test_export_row_helpers_emit_json_shape() -> None:
    now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    utterance = _utterance_row_to_json(
        (
            "candidate-1",
            "seed",
            "generated",
            0.7,
            False,
            now - timedelta(minutes=2),
            now + timedelta(minutes=3),
            None,
            None,
            1,
            "world_observation",
            ["motive:curiosity"],
            {"intrusion_risk": 0.2},
        ),
        now,
    )
    arrival = _arrival_row_to_json(
        (
            "arrival-1",
            "desk",
            now - timedelta(seconds=10),
            now + timedelta(minutes=1),
            {"schema_version": 1},
            "wait_silent",
            None,
            None,
        ),
        now,
    )

    assert utterance["lifecycle"] == "active"
    assert utterance["context_tags"] == ["motive:curiosity"]
    assert arrival["lifecycle"] == "fresh"
    assert arrival["device_id"] == "desk"


@pytest.mark.unit
def test_simulate_silence_fires_from_candidate_pressure() -> None:
    now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    candidate = CandidateView.from_payload(
        {
            "id": "candidate-1",
            "source": "world_observation",
            "seed": "画面の反復操作が気になる",
            "generated_text": "さっきから同じところ直してるね。",
            "priority": 1.0,
            "urgent": True,
            "maturity": 1,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=5)).isoformat(),
            "context_tags": ["motive:teasing"],
            "metadata_json": {"motive_strength": 1.0, "intrusion_risk": 0.0},
            "lifecycle": "active",
        }
    )

    result = simulate_silence(
        candidates=[candidate],
        start_ms=int(now.timestamp() * 1000),
        duration_sec=20,
        params={"threshold": 0.3, "teasing_gain": 0.8},
    )

    assert result["summary"]["fire_marker_count"] > 0
    assert result["fire_markers"][0]["dominant_motive"] == "teasing"


@pytest.mark.unit
def test_zero_gains_leave_only_explicit_score_terms() -> None:
    now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    candidate = CandidateView.from_payload(
        {
            "id": "candidate-1",
            "source": "diary",
            "seed": "言いそびれたこと",
            "generated_text": None,
            "priority": 1.0,
            "urgent": True,
            "maturity": 0,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=5)).isoformat(),
            "dismissed_at": (now + timedelta(minutes=4)).isoformat(),
            "context_tags": ["motive:unspoken"],
            "metadata_json": {"motive_strength": 1.0, "intrusion_risk": 0.0},
            "lifecycle": "dismissed",
        }
    )

    result = simulate_silence(
        candidates=[candidate],
        start_ms=int(now.timestamp() * 1000),
        duration_sec=3,
        params={
            "curiosity_gain": 0.0,
            "teasing_gain": 0.0,
            "attachment_gain": 0.0,
            "unspoken_gain": 0.0,
            "silence_attachment_gain": 0.0,
            "floor_weight": 0.0,
            "freshness_weight": 0.0,
            "intrusion_weight": 0.0,
            "threshold": 0.1,
        },
    )

    assert max(snapshot["speak_score"] for snapshot in result["snapshots"]) == 0.0


@pytest.mark.unit
def test_simulate_from_logs_marks_user_speech_as_not_floor_available() -> None:
    now_ms = 1_780_000_000_000
    result = simulate_from_logs(
        main_records=[
            {
                "ts_ms": now_ms,
                "lane": "main",
                "event": "final_transcript_received",
                "text": "ちょっと待って",
                "playback_state": "idle",
            }
        ],
        v2_records=[],
        candidates=[],
        params={"threshold": 0.2},
        step_sec=1.0,
    )

    first = result["snapshots"][0]
    assert first["user_speaking"] is True
    assert first["floor_available"] is False


@pytest.mark.unit
def test_prompt_preview_includes_motive_directive() -> None:
    snapshot = {
        "dominant_motive": "teasing",
        "speak_score": 0.7,
        "floor_available": True,
        "recent_text": "final_transcript_received: そこ直してる",
    }

    preview = build_prompt_preview(snapshot, None)

    assert "ちょっとちょっかい" in preview
    assert "OUTPUT CONTRACT" in preview


@pytest.mark.unit
def test_recent_session_groups_selects_latest_sessions() -> None:
    groups = recent_session_groups(
        main_records=[
            {"ts_ms": 1000, "conversation_session_id": "old", "text": "old"},
            {"ts_ms": 3000, "conversation_session_id": "new", "text": "new"},
        ],
        v2_records=[
            {"ts_ms": 3500, "conversation_session_id": "new", "stable_text": "new"}
        ],
        limit=1,
    )

    assert len(groups) == 1
    assert groups[0]["session_id"] == "new"
    assert groups[0]["event_count"] == 2
    assert groups[0]["main_records"][0]["text"] == "new"


@pytest.mark.unit
def test_simulate_recent_sessions_returns_selectable_payload() -> None:
    result = simulate_recent_sessions_from_logs(
        main_records=[
            {
                "ts_ms": 1000,
                "conversation_session_id": "session-a",
                "lane": "main",
                "event": "final_transcript_received",
                "text": "こんにちは",
            }
        ],
        v2_records=[],
        candidates=[],
        limit=100,
    )

    assert result["mode"] == "multi_session"
    assert result["session_count"] == 1
    assert result["sessions"][0]["session_id"] == "session-a"
    assert "simulation" in result["sessions"][0]


@pytest.mark.unit
def test_conversation_db_row_becomes_visible_timeline_event() -> None:
    recorded_at = datetime(2026, 6, 14, 1, 30, tzinfo=UTC)

    record = _conversation_row_to_record(
        (
            "session-1",
            recorded_at,
            "user",
            "seijiro",
            "タイムラインに会話を出したい",
            None,
            "invited",
            "completed",
        )
    )

    assert record["conversation_session_id"] == "session-1"
    assert record["lane"] == "main"
    assert record["event"] == "final_transcript_received"
    assert record["text"] == "タイムラインに会話を出したい"


@pytest.mark.unit
def test_render_html_separates_persona_and_timing_controls() -> None:
    html = render_html(
        {
            "schema_version": 1,
            "params": {},
            "events": [],
            "candidates": [],
            "snapshots": [],
            "summary": {},
        }
    )

    assert "Persona / Motive" in html
    assert "Timing / Gate" in html
    assert ".control-group.persona" in html
    assert ".control-group.timing" in html
    assert "control-group ${group.id}" in html


@pytest.mark.unit
def test_render_html_interleaves_fire_markers_with_timeline_events() -> None:
    html = render_html(
        {
            "schema_version": 1,
            "params": {},
            "events": [],
            "candidates": [],
            "snapshots": [],
            "summary": {},
        }
    )

    assert "const timelineItems = [" in html
    assert 'kind: "event"' in html
    assert 'kind: "fire"' in html
    assert ".sort((left, right) =>" in html
