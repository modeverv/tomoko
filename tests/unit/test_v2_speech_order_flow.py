from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from server.audio.stt import StreamingSttEvent
from server.hot_path.model_executor import StaticWavTtsBackend
from server.hot_path.speech_executor import SpeechOrderExecutor
from server.llm.chat import StaticChatBackend
from server.shared.models import (
    AppendDedupeDecision,
    AudioSpeechSegment,
    ConversationHistoryItem,
    PartialTranscriptObservation,
    PromptRequest,
    SemanticSaturationResult,
    SpeechOrder,
    SpeechOrderMode,
    TurnMaterials,
    utc_now,
)
from server.tomoko.conversation import TomokoConversationCore
from server.tomoko.scheduler import SpeechScheduler
from server.tomoko.semantic import SemanticSaturationJudge
from server.tomoko.session import SessionBoundaryModel

pytestmark = pytest.mark.unit


class FixedSaturationJudge:
    def __init__(self, saturation: float) -> None:
        self.saturation = saturation

    async def judge(self, text: str, *, partial: bool = False) -> SemanticSaturationResult:
        return SemanticSaturationResult(
            saturation=self.saturation,
            source="fixed_partial" if partial else "fixed_final",
            basis_text=text,
        )


class CountingChatBackend:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls = 0

    async def stream(self, request: PromptRequest):
        self.calls += 1
        text = self.replies.pop(0) if self.replies else "fallback"
        yield text


class FakeAppendDedupeGuard:
    def __init__(self, decisions: list[AppendDedupeDecision]) -> None:
        self.decisions = list(decisions)
        self.calls: list[tuple[str, str]] = []

    def inspect(
        self,
        *,
        previous_user_text: str,
        current_user_text: str,
        time_delta_ms: int,
        tomoko_speaking: bool,
        speech_queue_active: bool,
        current_is_final: bool,
    ) -> AppendDedupeDecision:
        self.calls.append((previous_user_text, current_user_text))
        if not self.decisions:
            return AppendDedupeDecision(
                previous_user_text=previous_user_text,
                current_user_text=current_user_text,
                time_delta_ms=time_delta_ms,
                duplicate_score=0.0,
                continuation_score=0.0,
                new_intent_score=1.0,
                label="new_intent",
                should_suppress=False,
                reason="fake pass",
                source="fake",
            )
        return self.decisions.pop(0)


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
async def test_tomoko_conversation_core_suppresses_duplicate_final_before_llm() -> None:
    now = utc_now()
    chat = CountingChatBackend(["最初だけ返す。", "二度目は呼ばれない。"])
    guard = FakeAppendDedupeGuard(
        [
            AppendDedupeDecision(
                previous_user_text="うんあんまりよくわかってない",
                current_user_text="あんまりよくわかってない",
                time_delta_ms=900,
                duplicate_score=0.993,
                continuation_score=0.11,
                new_intent_score=0.04,
                label="duplicate",
                should_suppress=True,
                reason="append dedupe duplicate score crossed suppress threshold",
                source="fake",
            )
        ]
    )
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=FixedSaturationJudge(0.95),
        scheduler=SpeechScheduler(),
        chat_backend=chat,
        append_dedupe_guard=guard,
    )

    first = await core.handle_observation(
        PartialTranscriptObservation(
            text="うんあんまりよくわかってない",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )
    second = await core.handle_observation(
        PartialTranscriptObservation(
            text="あんまりよくわかってない",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert first.speech_order is not None
    assert second.durable_utterance is not None
    assert second.speech_order is None
    assert second.prompt_request is None
    assert second.model_events == []
    assert chat.calls == 1
    assert guard.calls == [
        ("うんあんまりよくわかってない", "あんまりよくわかってない")
    ]
    assert second.scheduler_output.action == "suppress"
    assert second.scheduler_output.reason == (
        "append dedupe duplicate score crossed suppress threshold"
    )
    assert second.scheduler_output.score_breakdown["append_dedupe_duplicate_score"] == 0.993


@pytest.mark.asyncio
async def test_tomoko_conversation_core_keeps_continuation_and_new_intent_after_dedupe() -> None:
    now = utc_now()
    chat = CountingChatBackend(["最初。", "補足に返す。", "新しい意図に返す。"])
    guard = FakeAppendDedupeGuard(
        [
            AppendDedupeDecision(
                previous_user_text="あんまりよくわかってない",
                current_user_text="もう少し具体的に言うと設定ファイルの話",
                time_delta_ms=1200,
                duplicate_score=0.02,
                continuation_score=0.94,
                new_intent_score=0.1,
                label="continuation",
                should_suppress=False,
                reason="append dedupe pass",
                source="fake",
            ),
            AppendDedupeDecision(
                previous_user_text="もう少し具体的に言うと設定ファイルの話",
                current_user_text="ところで音量下げて",
                time_delta_ms=1200,
                duplicate_score=0.05,
                continuation_score=0.1,
                new_intent_score=0.92,
                label="new_intent",
                should_suppress=False,
                reason="append dedupe pass",
                source="fake",
            ),
        ]
    )
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=FixedSaturationJudge(0.95),
        scheduler=SpeechScheduler(),
        chat_backend=chat,
        append_dedupe_guard=guard,
    )

    await core.handle_observation(
        PartialTranscriptObservation(
            text="あんまりよくわかってない",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )
    continuation = await core.handle_observation(
        PartialTranscriptObservation(
            text="もう少し具体的に言うと設定ファイルの話",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )
    new_intent = await core.handle_observation(
        PartialTranscriptObservation(
            text="ところで音量下げて",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert continuation.speech_order is not None
    assert new_intent.speech_order is not None
    assert chat.calls == 3
    assert len(guard.calls) == 2


@pytest.mark.asyncio
async def test_tomoko_conversation_core_can_emit_early_order_from_partial_stt() -> None:
    now = utc_now()
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=FixedSaturationJudge(0.95),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(["先に答え始めるね。"]),
    )

    first = await core.handle_observation(
        PartialTranscriptObservation(
            text="その今の予定を教えて",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )
    second = await core.handle_observation(
        PartialTranscriptObservation(
            text="その今の予定を教えてください",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
            trace_id=first.observation.trace_id,
        )
    )

    assert first.durable_utterance is None
    assert first.speech_order is None
    assert first.prompt_request is None
    assert first.scheduler_output.reason == "partial start gate is waiting for confirmation"
    assert second.durable_utterance is None
    assert second.speech_order is not None
    assert second.speech_order.mode == SpeechOrderMode.REPLACE_CURRENT
    assert "partial" in second.saturation.source


@pytest.mark.asyncio
async def test_tomoko_conversation_core_uses_high_score_partial_below_saturation() -> None:
    now = utc_now()
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=FixedSaturationJudge(0.70),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(["前のめりに返すね。"]),
    )
    core.update_turn_materials(
        TurnMaterials(
            window_ms=200,
            user_speaking=True,
            speech_probability=0.1,
            silence_ms=0,
            playback_active=False,
            p_yielding=0.9,
            stt_partial="今日の予定を教えて",
        )
    )

    first = await core.handle_observation(
        PartialTranscriptObservation(
            text="今日の予定を教えて",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )
    second = await core.handle_observation(
        PartialTranscriptObservation(
            text="今日の予定を教えてください",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
            trace_id=first.observation.trace_id,
        )
    )

    assert first.saturation.saturation < 0.75
    assert first.scheduler_output.score >= 0.75
    assert first.scheduler_output.reason == "partial start gate is waiting for confirmation"
    assert second.speech_order is not None
    assert second.speech_order.text == "前のめりに返すね。"


@pytest.mark.asyncio
async def test_tomoko_conversation_core_holds_partial_when_text_conflicts() -> None:
    now = utc_now()
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=FixedSaturationJudge(0.95),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(["呼ばれないはず。"]),
    )

    first = await core.handle_observation(
        PartialTranscriptObservation(
            text="これは誰",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )
    second = await core.handle_observation(
        PartialTranscriptObservation(
            text="これはダブルで出てるのか",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
            trace_id=first.observation.trace_id,
        )
    )

    assert first.speech_order is None
    assert first.scheduler_output.reason == "partial start gate is waiting for confirmation"
    assert second.speech_order is None
    assert second.scheduler_output.reason == "partial start gate text changed too much"


@pytest.mark.asyncio
async def test_tomoko_conversation_core_reconciles_final_after_partial_order() -> None:
    now = utc_now()
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=FixedSaturationJudge(0.95),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(["先に答えるね。", "重複しないでね。"]),
    )

    first_partial = await core.handle_observation(
        PartialTranscriptObservation(
            text="その今の予定を教えて",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )
    partial = await core.handle_observation(
        PartialTranscriptObservation(
            text="その今の予定を教えてください",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
            trace_id=first_partial.observation.trace_id,
        )
    )
    final = await core.handle_observation(
        PartialTranscriptObservation(
            text="トモコ、今の予定を教えてください",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
            trace_id=partial.observation.trace_id,
        )
    )

    assert partial.speech_order is not None
    assert final.durable_utterance is not None
    assert final.speech_order is None
    assert final.prompt_request is None
    assert final.scheduler_output.reason == "final reconciled with active partial reply"

    stale_partial = await core.handle_observation(
        PartialTranscriptObservation(
            text="今の予定を教えてください",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
            trace_id=partial.observation.trace_id,
        )
    )

    assert stale_partial.speech_order is None
    assert stale_partial.prompt_request is None
    assert stale_partial.scheduler_output.reason == (
        "partial reconciled with active partial reply"
    )


@pytest.mark.asyncio
async def test_tomoko_conversation_core_discards_conflicting_partial_after_partial_order() -> None:
    now = utc_now()
    trace_id = uuid4()
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=FixedSaturationJudge(0.95),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(
            ["先に答えるね。", "矛盾した追撃は出さないでね。"]
        ),
    )

    first = await core.handle_observation(
        PartialTranscriptObservation(
            text="その今の予定を教えて",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
            trace_id=trace_id,
        )
    )
    active = await core.handle_observation(
        PartialTranscriptObservation(
            text="その今の予定を教えてください",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
            trace_id=trace_id,
        )
    )
    conflicting = await core.handle_observation(
        PartialTranscriptObservation(
            text="全然違う誤認識が伸びてきた",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
            trace_id=trace_id,
        )
    )

    assert first.speech_order is None
    assert active.speech_order is not None
    assert conflicting.speech_order is None
    assert conflicting.prompt_request is None
    assert conflicting.scheduler_output.reason == (
        "partial discarded after active partial reply in same trace"
    )


@pytest.mark.asyncio
async def test_tomoko_conversation_core_suppresses_conflicting_final_after_partial_order() -> None:
    now = utc_now()
    trace_id = uuid4()
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=FixedSaturationJudge(0.95),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(
            ["先に答えるね。", "finalで二重に返さないでね。"]
        ),
    )

    await core.handle_observation(
        PartialTranscriptObservation(
            text="その今の予定を教えて",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
            trace_id=trace_id,
        )
    )
    active = await core.handle_observation(
        PartialTranscriptObservation(
            text="その今の予定を教えてください",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
            trace_id=trace_id,
        )
    )
    final = await core.handle_observation(
        PartialTranscriptObservation(
            text="これは最終認識で別内容になった",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
            trace_id=trace_id,
        )
    )

    assert active.speech_order is not None
    assert final.durable_utterance is not None
    assert final.speech_order is None
    assert final.prompt_request is None
    assert final.scheduler_output.reason == (
        "final discarded after active partial reply in same trace"
    )


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
    assert executor.current_score == 0.0


@pytest.mark.asyncio
async def test_speech_order_executor_stop_playback_clears_queue_and_generation() -> None:
    executor = SpeechOrderExecutor(StaticWavTtsBackend([b"RIFFxxxxWAVEdata"]))
    current = SpeechOrder(
        text="話している途中",
        mode=SpeechOrderMode.REPLACE_CURRENT,
        reason="unit",
        priority=50,
    )
    queued = SpeechOrder(
        text="次に話す",
        mode=SpeechOrderMode.APPEND_AFTER_CURRENT,
        reason="unit",
        priority=40,
    )
    executor.begin_external_playback(current, score=0.6)
    executor.append_queue.append(queued)
    generation = executor.current_generation

    executor.stop_playback(reason="ui_stop")

    assert executor.current_generation == generation + 1
    assert executor.current_order is None
    assert executor.current_score == 0.0
    assert executor.append_queue == []


@pytest.mark.asyncio
async def test_speech_order_executor_can_protect_inflight_replace_audio() -> None:
    executor = SpeechOrderExecutor(
        StaticWavTtsBackend([b"RIFFxxxxWAVEdata"]),
        protect_inflight_replace=True,
    )
    current = SpeechOrder(
        text="partial reply",
        mode=SpeechOrderMode.REPLACE_CURRENT,
        reason="partial",
        priority=100,
    )
    executor.begin_external_playback(current, score=1.0)

    final_replace = SpeechOrder(
        text="final reply",
        mode=SpeechOrderMode.REPLACE_CURRENT,
        reason="final",
        priority=100,
    )
    result = await executor.execute(final_replace)

    assert result.queued is True
    assert result.audio_chunks == []
    assert executor.current_order == current


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
