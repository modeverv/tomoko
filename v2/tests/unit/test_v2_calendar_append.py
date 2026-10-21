from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from server.shared.models import (
    PartialTranscriptObservation,
    SemanticSaturationResult,
    SpeechOrderMode,
    UserStatusObservation,
    utc_now,
)
from server.tomoko.calendar import (
    calendar_notice_text,
    calendar_urgency_from_items,
    fake_calendar_provider_from_env_payload,
)
from server.tomoko.conversation import TomokoConversationCore
from server.tomoko.realtime import app as tomoko_realtime_app
from server.tomoko.scheduler import SpeechScheduler
from server.tomoko.session import SessionBoundaryModel
from server.tomoko.turn_state import TurnMaterialState

pytestmark = pytest.mark.unit


class FixedSaturationJudge:
    def __init__(self, saturation: float) -> None:
        self._saturation = saturation

    async def judge(self, text: str, *, partial: bool = False) -> SemanticSaturationResult:
        return SemanticSaturationResult(
            saturation=self._saturation,
            source="fixed",
            basis_text=text,
        )


class CountingChatBackend:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls = 0

    async def stream(self, request):
        self.calls += 1
        yield self.replies.pop(0) if self.replies else "fallback"


def _key(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M")


def test_calendar_urgency_from_items() -> None:
    now = utc_now()
    near = {_key(now + timedelta(minutes=5)): "定例会議"}
    far = {_key(now + timedelta(hours=2)): "遠い予定"}
    past = {_key(now - timedelta(minutes=10)): "過ぎた予定"}

    assert calendar_urgency_from_items(near, now=now) == pytest.approx(1 - 5 / 30, abs=0.05)
    assert calendar_urgency_from_items(far, now=now) == 0.0
    assert calendar_urgency_from_items(past, now=now) == 0.0
    assert calendar_urgency_from_items({}, now=now) == 0.0


def test_calendar_notice_text_mentions_time_and_title() -> None:
    text = calendar_notice_text("2026-07-04 13:00", "定例会議")
    assert "13:00" in text
    assert "定例会議" in text


def test_fake_calendar_provider_from_env_payload() -> None:
    provider = fake_calendar_provider_from_env_payload(
        [{"offset_min": 5, "title": "定例会議"}]
    )
    items = provider()
    assert len(items) == 1
    assert list(items.values()) == ["定例会議"]


def _core_with_calendar(offset_min: int = 5) -> tuple[TomokoConversationCore, CountingChatBackend]:
    chat = CountingChatBackend(["予定はこれだよ。"])
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=FixedSaturationJudge(0.95),
        scheduler=SpeechScheduler(),
        chat_backend=chat,
        calendar_items_provider=fake_calendar_provider_from_env_payload(
            [{"offset_min": offset_min, "title": "定例会議"}]
        ),
    )
    return core, chat


@pytest.mark.asyncio
async def test_core_appends_calendar_notice_after_final_reply() -> None:
    now = utc_now()
    core, _ = _core_with_calendar()

    result = await core.handle_observation(
        PartialTranscriptObservation(
            text="トモコ、今の作業どう思う",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert result.speech_order is not None
    assert result.speech_order.mode == SpeechOrderMode.REPLACE_CURRENT
    assert len(result.followup_orders) == 1
    followup = result.followup_orders[0]
    assert followup.mode == SpeechOrderMode.APPEND_AFTER_CURRENT
    assert "calendar" in followup.reason
    assert "定例会議" in followup.text


@pytest.mark.asyncio
async def test_core_does_not_reappend_same_calendar_item() -> None:
    now = utc_now()
    core, chat = _core_with_calendar()
    chat.replies.append("二回目の返事だよ。")

    first = await core.handle_observation(
        PartialTranscriptObservation(
            text="トモコ、今の作業どう思う",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )
    second = await core.handle_observation(
        PartialTranscriptObservation(
            text="ありがとう、そのままでいいよ",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert len(first.followup_orders) == 1
    assert second.followup_orders == []


@pytest.mark.asyncio
async def test_core_suppresses_calendar_followup_when_user_is_absent() -> None:
    now = utc_now()
    core, _ = _core_with_calendar()
    core.update_user_status(
        UserStatusObservation(
            present=False,
            activity_label="away",
            summary="away: no user detected",
            source="unit",
            confidence=0.95,
        )
    )

    result = await core.handle_observation(
        PartialTranscriptObservation(
            text="トモコ、今の作業どう思う",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert result.speech_order is not None
    assert result.followup_orders == []
    assert result.scheduler_output.score_breakdown[
        "pressure_world_user_absence"
    ] == pytest.approx(1.0)


def test_realtime_ws_sends_followup_calendar_speech_order() -> None:
    core, _ = _core_with_calendar()
    tomoko_realtime_app.state.turn_material_state = TurnMaterialState()
    tomoko_realtime_app.state.conversation_core = core
    now = utc_now()
    observation = PartialTranscriptObservation(
        text="トモコ、今の作業どう思う",
        is_final=True,
        stability=1.0,
        audio_started_at=now,
        audio_ended_at=now,
    )

    with TestClient(tomoko_realtime_app).websocket_connect("/internal/hot-path") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "stt_observation", **observation.to_dict()})
        ack = ws.receive_json()
        main_order = ws.receive_json()
        followup_order = ws.receive_json()

    assert ack["type"] == "stt_observation_ack"
    assert main_order["type"] == "speech_order"
    assert main_order["mode"] == "replace_current"
    assert followup_order["type"] == "speech_order"
    assert followup_order["mode"] == "append_after_current"
    assert "定例会議" in followup_order["text"]
