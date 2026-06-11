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


@pytest.mark.unit
async def test_session_provisional_inference_completes_successfully() -> None:
    from unittest.mock import patch, AsyncMock
    from uuid import uuid4
    from server.shared.models import TurnTakingV2Advisory, PartialTranscriptObservation, ThinkingEvent
    from datetime import datetime, UTC

    # Mock dependencies
    vad = MagicMock()
    store = AsyncMock()
    router = AsyncMock()
    thinking_mode = MagicMock()

    # Event stream yield mock
    async def dummy_think(backend, thinking_input):
        yield ThinkingEvent(type="text_delta", value="こんにちは")
    thinking_mode.think = dummy_think

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

    # mock memory gate & snapshot builder
    memory_gate = MagicMock()
    memory_gate.plan_retrieval.return_value = MagicMock(
        intent="chitchat",
        retrieve_long_term=False,
        retrieve_calendar=False,
    )
    memory_gate.filter_for_prompt.return_value = MagicMock(exposed_memories=[])

    snapshot_builder = AsyncMock()
    snapshot = MagicMock()
    snapshot.recent_turns = []
    snapshot.calendar_events = []
    snapshot_builder.build.return_value = snapshot

    session = TomoroSession(
        vad_processor=vad,
        send_event=MagicMock(),
        turn_taking_v2_store=store,
        router=router,
        thinking_mode=thinking_mode,
        context_snapshot_builder=snapshot_builder,
        memory_gate=memory_gate,
    )

    with patch("server.shared.turn_taking_logger.log_provisional_inference_event") as mock_log_event:
        await session._process_v2_advisory(advisory_id)

        # Wait for the async task to complete
        assert turn_id in session._provisional_replies
        task = session._provisional_replies[turn_id]["task"]
        await task

        assert session._provisional_replies[turn_id]["status"] == "valid"
        assert session._provisional_replies[turn_id]["response_text"] == "こんにちは"

        # Check complete event is logged
        assert mock_log_event.call_count == 2
        _, kwargs = mock_log_event.call_args_list[1]
        assert kwargs["conversation_session_id"] == session_id
        assert kwargs["turn_id"] == turn_id
        assert kwargs["event"] == "provisional_inference_complete"
        assert kwargs["text"] == "こんにちは"
        assert "successfully finished" in kwargs["reason"]


@pytest.mark.unit
async def test_session_provisional_inference_discarded_on_divergence_in_advisory() -> None:
    from unittest.mock import patch, AsyncMock
    from uuid import uuid4
    from server.shared.models import TurnTakingV2Advisory, PartialTranscriptObservation
    from datetime import datetime, UTC

    # Mock dependencies
    vad = MagicMock()
    store = AsyncMock()

    advisory_id1 = uuid4()
    advisory_id2 = uuid4()
    turn_id = uuid4()
    obs_id1 = uuid4()
    obs_id2 = uuid4()
    session_id = uuid4()

    mock_advisory1 = TurnTakingV2Advisory(
        id=advisory_id1,
        observation_id=obs_id1,
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

    # second advisory with different text (intent divergence)
    mock_advisory2 = TurnTakingV2Advisory(
        id=advisory_id2,
        observation_id=obs_id2,
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
        would_start_inference=False,
        reason="test",
    )

    mock_obs1 = PartialTranscriptObservation(
        id=obs_id1,
        conversation_session_id=session_id,
        turn_id=turn_id,
        revision=1,
        observed_at=datetime.now(UTC),
        vad_state="idle",
        attention_mode="engaged",
        raw_text="昨日の件です",
        filtered_text="昨日の件です",
        stable_text="昨日の件",
        unstable_tail="です",
        audio_level_db=-15.0,
        source="test",
    )

    mock_obs2 = PartialTranscriptObservation(
        id=obs_id2,
        conversation_session_id=session_id,
        turn_id=turn_id,
        revision=2,
        observed_at=datetime.now(UTC),
        vad_state="idle",
        attention_mode="engaged",
        raw_text="やっぱり明日の件",
        filtered_text="やっぱり明日の件",
        stable_text="やっぱり明日の",
        unstable_tail="件",
        audio_level_db=-15.0,
        source="test",
    )

    # store mock mapping
    async def mock_get_advisory(adv_id):
        if adv_id == advisory_id1:
            return mock_advisory1
        return mock_advisory2
    store.get_advisory = mock_get_advisory

    async def mock_get_observation(obs_id):
        if obs_id == obs_id1:
            return mock_obs1
        return mock_obs2
    store.get_observation = mock_get_observation

    session = TomoroSession(
        vad_processor=vad,
        send_event=MagicMock(),
        turn_taking_v2_store=store,
    )

    # manually set provisional_replies to simulate running task
    dummy_task = MagicMock()
    dummy_task.done.return_value = False
    session._provisional_replies[turn_id] = {
        "status": "pending",
        "response_text": "",
        "task": dummy_task,
        "stable_text": "昨日の件",
    }

    with patch("server.shared.turn_taking_logger.log_provisional_inference_event") as mock_log_event:
        await session._process_v2_advisory(advisory_id2)

        # Verify task is cancelled and status is discarded
        dummy_task.cancel.assert_called_once()
        assert session._provisional_replies[turn_id]["status"] == "discarded"

        # Verify log event
        mock_log_event.assert_called_once()
        kwargs = mock_log_event.call_args.kwargs
        assert kwargs["conversation_session_id"] == session_id
        assert kwargs["turn_id"] == turn_id
        assert kwargs["event"] == "provisional_inference_discarded"
        assert "intent diverged" in kwargs["reason"]


@pytest.mark.unit
async def test_session_provisional_inference_validated_on_final_transcript_matching() -> None:
    from unittest.mock import patch, AsyncMock
    from uuid import uuid4
    from server.shared.models import Transcript
    from datetime import datetime, UTC

    # Mock dependencies
    vad = MagicMock()
    store = AsyncMock()

    turn_id = uuid4()
    session_id = uuid4()

    session = TomoroSession(
        vad_processor=vad,
        send_event=MagicMock(),
        turn_taking_v2_store=store,
    )
    session.active_conversation_session_id = session_id

    dummy_task = MagicMock()
    session._provisional_replies[turn_id] = {
        "status": "valid",
        "response_text": "こんにちは",
        "task": dummy_task,
        "stable_text": "昨日の件",
    }

    transcript = Transcript(
        text="昨日の件です", # starts with "昨日の件"
        device_id="test-device",
        speaker="user",
        audio_level_db=-20.0,
        recorded_at=datetime.now(UTC),
        is_final=True,
    )

    with patch("server.shared.turn_taking_logger.log_provisional_inference_event") as mock_log_event:
        await session._evaluate_and_cleanup_provisional_reply(
            turn_id=turn_id,
            transcript=transcript,
            should_participate=True,
        )

        assert session._provisional_replies[turn_id]["status"] == "validated"
        mock_log_event.assert_called_once()
        kwargs = mock_log_event.call_args.kwargs
        assert kwargs["conversation_session_id"] == session_id
        assert kwargs["turn_id"] == turn_id
        assert kwargs["event"] == "provisional_inference_validated"
        assert kwargs["text"] == "こんにちは"
        assert "matched final transcript" in kwargs["reason"]
