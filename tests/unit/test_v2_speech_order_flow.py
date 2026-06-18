from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from server.audio.stt import StreamingSttEvent
from server.hot_path.model_executor import StaticWavTtsBackend
from server.hot_path.speech_executor import SpeechOrderExecutor
from server.llm.chat import StaticChatBackend
from server.shared.models import (
    AudioSpeechSegment,
    ConversationHistoryItem,
    PartialTranscriptObservation,
    SpeechOrder,
    SpeechOrderMode,
    utc_now,
)
from server.tomoko.conversation import TomokoConversationCore
from server.tomoko.scheduler import SpeechScheduler
from server.tomoko.semantic import SemanticSaturationJudge
from server.tomoko.session import SessionBoundaryModel

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_tomoko_conversation_core_turns_final_stt_into_speech_order() -> None:
    now = utc_now()
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(["了解。短く返すね。"]),
    )

    result = await core.handle_observation(
        PartialTranscriptObservation(
            text="トモコ、短く返事して",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert result.durable_utterance is not None
    assert result.scheduler_output.action == "replace_current"
    assert result.prompt_request is not None
    assert result.speech_order is not None
    assert result.speech_order.text == "了解。短く返すね。"
    assert result.speech_order.mode == SpeechOrderMode.REPLACE_CURRENT
    assert result.speech_order.reason == result.scheduler_output.reason
    assert result.model_events[-1].text == "了解。短く返すね。"
    assert result.prompt_request is not None
    assert "recent_user_raw=トモコ、短く返事して" not in result.prompt_request.prompt_text
    assert "CURRENT_USER_UTTERANCE" not in result.prompt_request.prompt_text
    assert "SESSION_TRANSCRIPT:\nuser: トモコ、短く返事して" in (
        result.prompt_request.prompt_text
    )


@pytest.mark.asyncio
async def test_tomoko_conversation_core_can_emit_early_order_from_partial_stt() -> None:
    now = utc_now()
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(["先に答え始めるね。"]),
    )

    result = await core.handle_observation(
        PartialTranscriptObservation(
            text="トモコ、今の予定を教えて",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert result.durable_utterance is None
    assert result.speech_order is not None
    assert result.speech_order.mode == SpeechOrderMode.REPLACE_CURRENT
    assert "partial" in result.saturation.source


@pytest.mark.asyncio
async def test_tomoko_conversation_core_turns_stop_intent_into_stop_order() -> None:
    now = utc_now()
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(["このテキストは使われない"]),
    )

    result = await core.handle_observation(
        PartialTranscriptObservation(
            text="トモコ、止めて",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert result.speech_order is not None
    assert result.speech_order.mode == SpeechOrderMode.STOP
    assert result.speech_order.text == ""


@pytest.mark.asyncio
async def test_tomoko_conversation_core_uses_same_session_history_without_duplication() -> None:
    now = utc_now()
    session_id = uuid4()
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(["今は短く整っているよ。"]),
    )

    result = await core.handle_observation(
        PartialTranscriptObservation(
            text="最後に今の状態を短くまとめて",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        ),
        session_id_override=session_id,
        prior_session_history=[
            ConversationHistoryItem(speaker="user", text="最初に短く返事して"),
            ConversationHistoryItem(speaker="tomoko", text="了解。短く話すね。"),
            ConversationHistoryItem(speaker="user", text="今の返事ちゃんと聞こえてる"),
            ConversationHistoryItem(speaker="tomoko", text="うん、ちゃんと聞こえてるよ。"),
        ],
    )

    assert result.durable_utterance is not None
    assert result.durable_utterance.session_id == session_id
    assert result.context_snapshot is not None
    assert result.context_snapshot.session_id == session_id
    assert result.prompt_request is not None
    prompt = result.prompt_request.prompt_text
    assert "user: 最初に短く返事して" in prompt
    assert "tomoko: 了解。短く話すね。" in prompt
    assert "user: 最後に今の状態を短くまとめて" in prompt
    assert "CURRENT_USER_UTTERANCE" not in prompt
    assert "recent_user_raw=最後に今の状態を短くまとめて" not in prompt


@pytest.mark.asyncio
async def test_speech_order_executor_replace_append_stop_and_generation_guard() -> None:
    executor = SpeechOrderExecutor(
        StaticWavTtsBackend([b"RIFF1111WAVEdata", b"RIFF2222WAVEdata"])
    )
    first = SpeechOrder(
        text="最初",
        mode=SpeechOrderMode.REPLACE_CURRENT,
        reason="unit",
        priority=50,
    )
    replaced = await executor.execute(first)
    assert [chunk.chunk for chunk in replaced.audio_chunks] == [
        b"RIFF1111WAVEdata",
        b"RIFF2222WAVEdata",
    ]
    assert replaced.audio_chunks[-1].is_final

    generation = executor.current_generation
    executor.replace_generation()
    assert not executor.is_current_generation(generation)

    executor.begin_external_playback(first, score=0.4)
    appended_order = SpeechOrder(
        text="予定通知",
        mode=SpeechOrderMode.APPEND_AFTER_CURRENT,
        reason="calendar",
        priority=70,
    )
    queued = await executor.execute(appended_order)
    assert queued.audio_chunks == []
    assert executor.append_queue == [appended_order]

    stopped = await executor.execute(
        SpeechOrder(text="", mode=SpeechOrderMode.STOP, reason="user stop", priority=100)
    )
    assert stopped.audio_chunks == []
    assert executor.append_queue == []
    assert executor.current_order is None


@pytest.mark.asyncio
async def test_in_process_vertical_slice_stt_to_speech_order_to_audio() -> None:
    now = utc_now()
    segment = AudioSpeechSegment(
        samples=(0.2,) * 1600,
        sample_rate=16000,
        started_at=now,
        ended_at=now,
    )
    stt_event = StreamingSttEvent("トモコ、短く返事して", True, 1.0)
    observation = PartialTranscriptObservation(
        text=stt_event.text,
        is_final=stt_event.is_final,
        stability=stt_event.stability,
        audio_started_at=segment.started_at,
        audio_ended_at=segment.ended_at,
        trace_id=segment.trace_id,
    )
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(["うん、聞こえてるよ。"]),
    )
    executor = SpeechOrderExecutor(StaticWavTtsBackend([b"RIFFxxxxWAVEdata"]))

    turn = await core.handle_observation(observation)
    assert turn.speech_order is not None
    audio = await executor.execute(turn.speech_order)

    assert turn.saturation.saturation > 0
    assert turn.scheduler_output.score_breakdown
    assert audio.audio_chunks[0].chunk == b"RIFFxxxxWAVEdata"


def test_scheduler_report_script_exists_after_s12() -> None:
    assert Path("scripts/v2_scheduler_report.py").exists()
