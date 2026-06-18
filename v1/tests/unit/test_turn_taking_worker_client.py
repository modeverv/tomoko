from __future__ import annotations

import httpx
import pytest

from server.gateway.turn_taking.worker_client import TurnTakingWorkerClient
from server.shared.models import TurnTakingAudioMetrics, TurnTakingInput


def _input(text: str) -> TurnTakingInput:
    return TurnTakingInput(
        pending_reply_state="generating_not_started",
        new_transcript=text,
        audio_metrics=TurnTakingAudioMetrics(
            segment_ms=500,
            rms_db=-24,
            peak_db=-12,
            active_frame_ratio=0.8,
        ),
        attention_mode="engaged",
        playback_state="idle",
    )


@pytest.mark.unit
async def test_worker_client_uses_rule_preflight_for_clear_stop_word() -> None:
    client = TurnTakingWorkerClient(url="http://127.0.0.1:1/judge", timeout_ms=1)

    decision = await client.judge(_input("ストップ"))

    assert decision.decision == "stop_speaking"
    assert decision.source == "rule"


@pytest.mark.unit
async def test_worker_client_falls_back_to_rule_when_worker_unavailable(monkeypatch) -> None:
    async def fake_post(self, url: str, *, json: dict[str, object]):
        del self, url, json
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    client = TurnTakingWorkerClient(url="http://127.0.0.1:8765/judge", timeout_ms=1)

    decision = await client.judge(_input("えっと"))

    assert decision.decision == "defer_output"
    assert decision.source == "rule_fallback"
    assert decision.reason.startswith("worker_error:")
