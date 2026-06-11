from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from server.session import TomoroSession
from server.shared.models import Transcript, TranscriptFilterDecision
from server.shared.turn_taking_v2 import NullTurnTakingV2Store


@pytest.mark.unit
async def test_null_turn_taking_v2_store() -> None:
    store = NullTurnTakingV2Store()
    obs_id = await store.save_observation(
        conversation_session_id=None,
        turn_id=None,
        revision=0,
        vad_state="listening",
        attention_mode="engaged",
        raw_text="hello",
        filtered_text="hello",
        stable_text=None,
        unstable_tail=None,
        audio_level_db=-30.0,
        source="test",
    )
    assert obs_id is not None
    obs = await store.get_observation(obs_id)
    assert obs is None

    adv_id = await store.save_advisory(
        observation_id=obs_id,
        conversation_session_id=None,
        turn_id=None,
        semantic_saturation=0.5,
        remaining_info_risk=0.5,
        semantic_split_risk=0.1,
        speech_decision_score=0.3,
        safe_response_level=1,
        proposal="silence",
        confidence=0.5,
        reason="dummy",
    )
    assert adv_id is not None
    adv = await store.get_advisory(adv_id)
    assert adv is None


@pytest.mark.unit
async def test_session_saves_observation_on_partial_transcript() -> None:
    vad = MagicMock()
    vad.sample_rate = 16000
    vad.device_id = "test-device"
    vad_result = MagicMock()
    vad_result.state_changed_to = None
    vad_result.segment = None
    vad.process_chunk.return_value = vad_result

    transcriber = AsyncMock()
    partial = Transcript(
        text="test partial text",
        device_id="test-device",
        speaker="user",
        audio_level_db=-25.0,
        recorded_at=MagicMock(),
        is_final=False,
    )
    transcriber.process_stream_chunk.return_value = partial

    store = AsyncMock()
    store.save_observation = AsyncMock()
    store.dsn = None

    tx_filter = MagicMock()
    tx_filter.evaluate.return_value = TranscriptFilterDecision(
        action="accept", reason="ok"
    )

    frontend = MagicMock()
    frontend.should_process_partial_chunk.return_value = True

    session = TomoroSession(
        vad_processor=vad,
        send_event=MagicMock(),
        transcriber=transcriber,
        turn_taking_v2_store=store,
        transcript_filter=tx_filter,
        stt_audio_frontend=frontend,
    )

    chunk = np.zeros(512, dtype=np.float32)
    session.state = "listening"

    await session.process_audio_chunk(chunk.tobytes())

    store.save_observation.assert_called_once()
    kwargs = store.save_observation.call_args.kwargs
    assert kwargs["raw_text"] == "test partial text"
    assert kwargs["filtered_text"] == "test partial text"
    assert kwargs["revision"] == 0
