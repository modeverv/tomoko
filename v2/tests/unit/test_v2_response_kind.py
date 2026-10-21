from __future__ import annotations

import pytest

from server.shared.models import ResponseKind, SpeechOrder, SpeechOrderMode

pytestmark = pytest.mark.unit


def test_response_kind_values_are_fixed() -> None:
    assert {kind.value for kind in ResponseKind} == {
        "backchannel",
        "acknowledgement",
        "content",
        "correction",
        "followup",
    }


def test_non_stop_speech_order_requires_response_kind() -> None:
    with pytest.raises(ValueError, match="response_kind is required"):
        SpeechOrder(
            text="hello",
            mode=SpeechOrderMode.REPLACE_CURRENT,
            reason="test",
            priority=50,
        )


def test_stop_speech_order_allows_response_kind_none() -> None:
    order = SpeechOrder(
        text="",
        mode=SpeechOrderMode.STOP,
        reason="user stop",
        priority=100,
    )

    assert order.response_kind is None


def test_speech_order_round_trip_keeps_response_kind() -> None:
    order = SpeechOrder(
        text="hello",
        mode=SpeechOrderMode.REPLACE_CURRENT,
        reason="test",
        priority=50,
        response_kind=ResponseKind.CONTENT,
    )

    assert SpeechOrder.from_dict(order.to_dict()).response_kind is ResponseKind.CONTENT


def test_unknown_response_kind_is_parse_error() -> None:
    order = SpeechOrder(
        text="hello",
        mode=SpeechOrderMode.REPLACE_CURRENT,
        reason="test",
        priority=50,
        response_kind=ResponseKind.CONTENT,
    ).to_dict()
    order["response_kind"] = "unknown"

    with pytest.raises(ValueError):
        SpeechOrder.from_dict(order)
