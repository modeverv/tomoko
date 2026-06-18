from __future__ import annotations

import pytest

from server.gateway.reply.display import ReplyDisplayPlanner


@pytest.mark.unit
def test_reply_display_planner_updates_emotion_and_image_together() -> None:
    planner = ReplyDisplayPlanner()

    display = planner.update_emotion("happy")

    assert planner.current_emotion == "happy"
    assert display.emotion == "happy"
    assert display.image == "/assets/images/tomoko-happy.svg"


@pytest.mark.unit
def test_reply_display_planner_falls_back_to_neutral_image() -> None:
    planner = ReplyDisplayPlanner()

    display = planner.update_emotion("unknown")

    assert display.emotion == "unknown"
    assert display.image == "/assets/images/tomoko-neutral.svg"
