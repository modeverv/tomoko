from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from server.session_payloads import (
    json_safe_payload,
    optional_float_payload,
    optional_int_payload,
    optional_str_payload,
    playback_payload,
    playback_telemetry_from_event,
)
from server.shared.models import SessionEvent


@pytest.mark.unit
def test_json_safe_payload_preserves_shape_and_converts_uuid_datetime() -> None:
    event_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    occurred_at = datetime(2026, 5, 29, 12, 34, 56, tzinfo=UTC)

    payload = json_safe_payload(
        {
            "id": event_id,
            "occurred_at": occurred_at,
            "nested": {1: ("keep", event_id)},
        }
    )

    assert payload == {
        "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "occurred_at": "2026-05-29T12:34:56+00:00",
        "nested": {"1": ["keep", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"]},
    }


@pytest.mark.unit
def test_playback_helpers_keep_payload_contract_and_coerce_telemetry() -> None:
    event = SessionEvent(
        type="playback_started",
        payload={
            "turn_id": 42,
            "chunk_id": "7",
            "scheduled_audio_time": "1.25",
            "sent_audio_time": 2,
            "audio_context_time": None,
            "performance_now_ms": "300.5",
            "ignored": "kept out",
        },
    )

    telemetry = playback_telemetry_from_event(event)

    assert playback_payload(event) == {"turn_id": 42, "chunk_id": "7"}
    assert telemetry.type == "playback_started"
    assert telemetry.turn_id == "42"
    assert telemetry.chunk_id == 7
    assert telemetry.scheduled_audio_time == 1.25
    assert telemetry.sent_audio_time == 2.0
    assert telemetry.audio_context_time is None
    assert telemetry.performance_now_ms == 300.5


@pytest.mark.unit
def test_playback_telemetry_from_event_rejects_non_playback_event() -> None:
    with pytest.raises(ValueError, match="not a playback event"):
        playback_telemetry_from_event(SessionEvent(type="idle_timer_elapsed"))


@pytest.mark.unit
def test_optional_payload_coercion_matches_session_payload_semantics() -> None:
    assert optional_str_payload(None) is None
    assert optional_str_payload(123) == "123"
    assert optional_int_payload(None) is None
    assert optional_int_payload("12") == 12
    assert optional_float_payload(None) is None
    assert optional_float_payload("1.5") == 1.5
