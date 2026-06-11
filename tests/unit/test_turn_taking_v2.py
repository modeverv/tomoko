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
        would_start_inference=False,
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


@pytest.mark.unit
async def test_session_triggers_provisional_inference_on_would_start_inference_advisory() -> None:
    from unittest.mock import patch, AsyncMock
    from uuid import uuid4
    from server.shared.models import TurnTakingV2Advisory, PartialTranscriptObservation
    from datetime import datetime, UTC

    # Mock dependencies
    vad = MagicMock()
    store = AsyncMock()
    
    advisory_id = uuid4()
    turn_id = uuid4()
    obs_id = uuid4()
    session_id = uuid4()

    mock_advisory = TurnTakingV2Advisory(
        id=advisory_id,
        observation_id=obs_id,
        conversation_session_id=session_id,
        turn_id=turn_id,
        created_at=datetime.now(UTC),
        semantic_saturation=0.85,
        remaining_info_risk=0.15,
        semantic_split_risk=0.05,
        speech_decision_score=0.78,
        safe_response_level=4,
        proposal="full_response_candidate",
        confidence=0.8,
        would_start_inference=True,
        reason="test",
    )
    store.get_advisory.return_value = mock_advisory

    mock_obs = PartialTranscriptObservation(
        id=obs_id,
        conversation_session_id=session_id,
        turn_id=turn_id,
        revision=1,
        observed_at=datetime.now(UTC),
        vad_state="idle",
        attention_mode="engaged",
        raw_text="テスト安定部分、です",
        filtered_text="テスト安定部分、です",
        stable_text="テスト安定部分",
        unstable_tail="、です",
        audio_level_db=-15.0,
        source="test",
    )
    store.get_observation.return_value = mock_obs

    session = TomoroSession(
        vad_processor=vad,
        send_event=MagicMock(),
        turn_taking_v2_store=store,
    )

    with patch("server.shared.turn_taking_logger.log_provisional_inference_start") as mock_log_prov:
        await session._process_v2_advisory(advisory_id)
        
        # Check that provisional inference timestamp is recorded
        assert turn_id in session._v2_provisional_inference_started_at
        assert isinstance(session._v2_provisional_inference_started_at[turn_id], int)

        # Check that log_provisional_inference_start was called with correct args
        mock_log_prov.assert_called_once()
        kwargs = mock_log_prov.call_args.kwargs
        assert kwargs["conversation_session_id"] == session_id
        assert kwargs["turn_id"] == turn_id
        assert kwargs["stable_text"] == "テスト安定部分"
        assert "would_start_inference=True" in kwargs["reason"]

        # Call again to test deduplication (should not log again)
        mock_log_prov.reset_mock()
        await session._process_v2_advisory(advisory_id)
        mock_log_prov.assert_not_called()
