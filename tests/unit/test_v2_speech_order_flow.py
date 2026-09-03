from __future__ import annotations

import asyncio
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
    CandidateLifecycle,
    CandidateRecord,
    ConversationHistoryItem,
    PartialTranscriptObservation,
    PersonalityMaterials,
    PromptRequest,
    PromptScope,
    ResponseKind,
    SemanticSaturationResult,
    SpeechOrder,
    SpeechOrderMode,
    TurnMaterials,
    UserStatusObservation,
    WorldMaterials,
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
        chat_backend=StaticChatBackend(["了解、短く返すね。"]),
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
    assert result.speech_order.text == "了解、短く返すね。"
    assert result.speech_order.mode == SpeechOrderMode.REPLACE_CURRENT
    assert result.speech_order.reason == result.scheduler_output.reason
    assert result.model_events[-1].text == "了解、短く返すね。"
    assert result.prompt_request is not None
    assert "recent_user_raw=トモコ、短く返事して" not in result.prompt_request.prompt_text
    assert "CURRENT_USER_UTTERANCE" not in result.prompt_request.prompt_text
    assert "SESSION_TRANSCRIPT:\nuser: トモコ、短く返事して" in (
        result.prompt_request.prompt_text
    )


@pytest.mark.asyncio
async def test_tomoko_conversation_core_speaks_first_sentence_and_queues_continuation() -> None:
    class ChunkedChatBackend:
        def __init__(self) -> None:
            self.yielded = 0
            self.chunks = ["最初の", "一文。二文目。", "三文目。"]

        async def stream(self, _request: PromptRequest):
            for chunk in self.chunks:
                self.yielded += 1
                yield chunk

    now = utc_now()
    chat = ChunkedChatBackend()
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=chat,
    )

    result = await core.handle_observation(
        PartialTranscriptObservation(
            text="トモコ、今日の話を聞かせて",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert chat.yielded == 2
    assert result.speech_order is not None
    assert result.speech_order.text == "最初の一文。"
    assert result.model_events[-1].text == "最初の一文。"
    assert not result.followup_orders
    assert core._continuation_task is not None
    await asyncio.wait_for(core._continuation_task, timeout=1.0)
    orders, pending = core.poll_followup_orders()
    assert not pending
    assert len(orders) == 1
    assert orders[0].text == "二文目。三文目。"
    assert orders[0].mode == SpeechOrderMode.APPEND_AFTER_CURRENT
    assert orders[0].reason == "reply continuation queued after first sentence"
    again, pending_again = core.poll_followup_orders()
    assert not again and not pending_again


@pytest.mark.asyncio
async def test_tomoko_conversation_core_keeps_partial_reply_to_first_sentence() -> None:
    class ChunkedChatBackend:
        def __init__(self) -> None:
            self.yielded = 0
            self.chunks = ["最初の", "一文。二文目。", "三文目。"]

        async def stream(self, _request: PromptRequest):
            for chunk in self.chunks:
                self.yielded += 1
                yield chunk

    now = utc_now()
    chat = ChunkedChatBackend()
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=FixedSaturationJudge(0.95),
        scheduler=SpeechScheduler(),
        chat_backend=chat,
    )

    result = await core.handle_observation(
        PartialTranscriptObservation(
            text="その今の予定を教えて",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
            p_yielding=0.92,
        )
    )

    assert chat.yielded == 2
    assert result.speech_order is not None
    assert result.speech_order.text == "最初の一文。"
    assert result.model_events[-1].text == "最初の一文。"
    assert not result.followup_orders
    orders, pending = core.poll_followup_orders()
    assert not orders and not pending


@pytest.mark.asyncio
async def test_tomoko_conversation_core_kicks_screenshot_sense_and_appends_followup() -> None:
    now = utc_now()
    executed: list[str] = []

    async def fake_sense_executor(record) -> dict[str, object]:
        executed.append(record.kind)
        return {
            "present": True,
            "activity_label": "coding_or_terminal",
            "summary": "エディタで conversation.py を開いて作業中",
        }

    chat = CountingChatBackend(["コード書いてるみたいだね、conversation.pyを開いてるよ。"])
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=chat,
        sense_executor=fake_sense_executor,
    )

    result = await core.handle_observation(
        PartialTranscriptObservation(
            text="トモコ、今の画面で何してるか教えて",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert result.speech_order is not None
    assert result.speech_order.text == "ちょっと画面見てみるね。"
    assert result.speech_order.reason == "screenshot sense kicked for user context"
    assert result.speech_order.response_kind == ResponseKind.ACKNOWLEDGEMENT
    assert chat.calls == 0

    assert core._sense_task is not None
    await asyncio.wait_for(core._sense_task, timeout=1.0)
    assert executed == ["screenshot"]
    orders, pending = core.poll_followup_orders()
    assert not pending
    assert len(orders) == 1
    assert orders[0].mode == SpeechOrderMode.APPEND_AFTER_CURRENT
    assert orders[0].reason == "sense result follow-up after screenshot"
    assert orders[0].response_kind == ResponseKind.FOLLOWUP
    assert "conversation.py" in orders[0].text
    assert chat.calls == 1
    assert core.user_status is not None
    assert core.user_status.source == "sense_screenshot"


@pytest.mark.asyncio
async def test_tomoko_conversation_core_kicks_world_search_sense_and_appends_followup() -> None:
    now = utc_now()
    kinds: list[str] = []

    async def fake_sense_executor(record) -> dict[str, object]:
        kinds.append(record.kind)
        return {"texts": ["明日の天気は雨のち晴れ、最高気温は28度。"]}

    chat = CountingChatBackend(["明日は雨のち晴れで28度まで上がるみたいだよ。"])
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=chat,
        sense_executor=fake_sense_executor,
    )

    result = await core.handle_observation(
        PartialTranscriptObservation(
            text="トモコ、明日の天気を調べて",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert result.speech_order is not None
    assert result.speech_order.text == "ちょっと調べてみるね。"
    assert result.speech_order.reason == "world search sense kicked for user request"

    assert core._sense_task is not None
    await asyncio.wait_for(core._sense_task, timeout=1.0)
    assert kinds == ["world_search"]
    orders, pending = core.poll_followup_orders()
    assert not pending
    assert len(orders) == 1
    assert orders[0].reason == "sense result follow-up after world search"
    assert "28度" in orders[0].text


@pytest.mark.asyncio
async def test_tomoko_conversation_core_apologizes_when_world_search_returns_nothing() -> None:
    now = utc_now()

    async def failing_sense_executor(record) -> None:
        return None

    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=CountingChatBackend(["呼ばれない。"]),
        sense_executor=failing_sense_executor,
    )

    result = await core.handle_observation(
        PartialTranscriptObservation(
            text="トモコ、明日の天気を調べて",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert result.speech_order is not None
    assert result.speech_order.text == "ちょっと調べてみるね。"
    assert core._sense_task is not None
    await asyncio.wait_for(core._sense_task, timeout=1.0)
    orders, pending = core.poll_followup_orders()
    assert not pending
    assert len(orders) == 1
    assert orders[0].text == "ごめん、うまく調べられなかったよ。"
    assert orders[0].reason == "world search sense returned no result"


@pytest.mark.asyncio
async def test_tomoko_conversation_core_skips_sense_kick_without_executor() -> None:
    now = utc_now()
    chat = CountingChatBackend(["普通に答えるよ。"])
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=chat,
    )

    result = await core.handle_observation(
        PartialTranscriptObservation(
            text="トモコ、今の画面で何してるか教えて",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert result.speech_order is not None
    assert result.speech_order.text == "普通に答えるよ。"
    assert chat.calls == 1


@pytest.mark.asyncio
async def test_tomoko_conversation_core_answers_clock_question_without_llm() -> None:
    now = utc_now()
    chat = CountingChatBackend(["LLMは呼ばれない。"])
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=chat,
    )

    result = await core.handle_observation(
        PartialTranscriptObservation(
            text="トモコ、今何時か教えて",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert result.speech_order is not None
    assert result.speech_order.text.startswith("今は")
    assert result.speech_order.text.endswith("分だよ。")
    assert result.speech_order.reason == "direct clock reply from local system time"
    assert result.prompt_request is not None
    assert result.model_events[-1].text == result.speech_order.text
    assert chat.calls == 0


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
            p_yielding=0.92,
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
    assert first.speech_order is not None
    assert first.speech_order.mode == SpeechOrderMode.REPLACE_CURRENT
    assert first.prompt_request is not None
    assert "partial" in first.saturation.source
    assert second.durable_utterance is None
    assert second.speech_order is None
    assert second.scheduler_output.reason == "partial reconciled with active partial reply"


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
            p_yielding=0.92,
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
    assert first.scheduler_output.score_breakdown[
        "pressure_dialogue_yielding_opportunity"
    ] == pytest.approx(0.9)
    assert first.scheduler_output.score_breakdown[
        "pressure_dialogue_silence_opportunity"
    ] == pytest.approx(0.0)
    assert first.scheduler_output.score_breakdown[
        "pressure_dialogue_turn_opportunity_from_yielding"
    ] == pytest.approx(1.0)
    assert first.scheduler_output.score_breakdown[
        "pressure_dialogue_turn_opportunity_from_silence"
    ] == pytest.approx(0.0)
    assert first.speech_order is not None
    assert first.speech_order.text == "前のめりに返すね。"
    assert second.speech_order is None
    assert second.scheduler_output.reason == "partial reconciled with active partial reply"


@pytest.mark.asyncio
async def test_tomoko_conversation_core_score_breakdown_marks_silence_fallback_origin() -> None:
    now = utc_now()
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=FixedSaturationJudge(0.95),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(["無音を見て返すね。"]),
    )
    core.update_turn_materials(
        TurnMaterials(
            window_ms=200,
            user_speaking=False,
            speech_probability=0.0,
            silence_ms=900,
            playback_active=False,
            p_yielding=None,
            stt_partial="",
        )
    )

    result = await core.handle_observation(
        PartialTranscriptObservation(
            text="今日の予定を教えてください",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    breakdown = result.scheduler_output.score_breakdown
    assert breakdown["pressure_dialogue_yielding_opportunity"] == pytest.approx(0.0)
    assert breakdown["pressure_dialogue_silence_opportunity"] == pytest.approx(0.75)
    assert breakdown["pressure_dialogue_turn_opportunity_from_yielding"] == pytest.approx(0.0)
    assert breakdown["pressure_dialogue_turn_opportunity_from_silence"] == pytest.approx(1.0)
    assert result.speech_order is not None


@pytest.mark.asyncio
async def test_tomoko_conversation_core_emits_short_motivation_interjection_before_final() -> None:
    now = utc_now()
    trace_id = uuid4()
    chat = CountingChatBackend(["本回答を短く返すね。"])
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=FixedSaturationJudge(0.45),
        scheduler=SpeechScheduler(),
        chat_backend=chat,
    )
    core.personality_materials = PersonalityMaterials(
        talkativeness=0.95,
        curiosity=0.95,
        restraint=0.05,
        empathy=0.8,
        interrupt_tolerance=0.85,
    )
    core.update_turn_materials(
        TurnMaterials(
            window_ms=200,
            user_speaking=True,
            speech_probability=0.1,
            p_yielding=0.9,
            silence_ms=0,
            playback_active=False,
            stt_partial="予定、それ違う気が",
        )
    )
    recent_history = [
        ConversationHistoryItem(speaker="user", text="今日の予定を見てる"),
        ConversationHistoryItem(speaker="tomoko", text="予定まわり、少し詰まってるね。"),
        ConversationHistoryItem(speaker="user", text="会議の予定が多い"),
        ConversationHistoryItem(speaker="tomoko", text="その予定なら先に整理したい。"),
    ]

    partial = await core.handle_observation(
        PartialTranscriptObservation(
            text="予定、それ違う気が",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
            p_yielding=0.9,
            trace_id=trace_id,
        ),
        prior_session_history=recent_history,
    )
    assert partial.speech_order is not None
    assert partial.speech_order.text == "いや、それってさ。"
    assert partial.speech_order.mode == SpeechOrderMode.REPLACE_CURRENT
    assert partial.speech_order.response_kind == ResponseKind.ACKNOWLEDGEMENT
    assert partial.scheduler_output.reason == (
        "motivation interjection before complete request"
    )
    assert partial.prompt_request is not None
    assert partial.prompt_request.scope == PromptScope.SHORT
    assert partial.scheduler_output.score_breakdown["motivation_interjection"] == 1.0
    assert partial.scheduler_output.score_breakdown[
        "pressure_motivation_threshold_shift"
    ] >= 0.12
    assert chat.calls == 0

    final = await core.handle_observation(
        PartialTranscriptObservation(
            text="トモコ、予定のどこが違うかちゃんと教えて",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
            trace_id=trace_id,
        ),
        prior_session_history=recent_history,
    )

    assert final.speech_order is not None
    assert final.speech_order.mode == SpeechOrderMode.REPLACE_CURRENT
    assert final.speech_order.text == "本回答を短く返すね。"
    assert final.scheduler_output.reason != "final reconciled with active partial reply"
    assert chat.calls == 1


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
async def test_tomoko_conversation_core_acknowledges_near_threshold_incomplete_partial() -> None:
    now = utc_now()
    chat = CountingChatBackend(["本回答を短く返すね。"])
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=FixedSaturationJudge(0.60),
        scheduler=SpeechScheduler(),
        chat_backend=chat,
    )
    core.update_turn_materials(
        TurnMaterials(
            window_ms=200,
            user_speaking=True,
            speech_probability=0.2,
            p_yielding=0.2,
            silence_ms=0,
            playback_active=False,
            stt_partial="今日の予定",
        )
    )

    partial = await core.handle_observation(
        PartialTranscriptObservation(
            text="今日の予定",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )
    final = await core.handle_observation(
        PartialTranscriptObservation(
            text="トモコ、今日の予定を教えて",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
            trace_id=partial.observation.trace_id,
        )
    )

    assert partial.speech_order is not None
    assert partial.speech_order.text == "うん、聞いてるよ。"
    assert partial.speech_order.mode == SpeechOrderMode.REPLACE_CURRENT
    assert partial.speech_order.response_kind == ResponseKind.ACKNOWLEDGEMENT
    assert partial.prompt_request is not None
    assert partial.scheduler_output.reason == (
        "partial acknowledgement before complete request"
    )
    assert chat.calls == 1
    assert final.speech_order is not None
    assert final.speech_order.mode == SpeechOrderMode.REPLACE_CURRENT
    assert final.speech_order.text == "本回答を短く返すね。"
    assert final.scheduler_output.reason != "final reconciled with active partial reply"


@pytest.mark.asyncio
async def test_tomoko_conversation_core_acknowledges_start_gate_wait_after_pause() -> None:
    now = utc_now()
    chat = CountingChatBackend(["本回答を短く返すね。"])
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=FixedSaturationJudge(0.60),
        scheduler=SpeechScheduler(),
        chat_backend=chat,
    )
    core.update_turn_materials(
        TurnMaterials(
            window_ms=200,
            user_speaking=True,
            speech_probability=0.16,
            p_yielding=0.34,
            silence_ms=2200,
            playback_active=False,
            stt_partial="今日の予定",
        )
    )

    partial = await core.handle_observation(
        PartialTranscriptObservation(
            text="今日の予定",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )
    final = await core.handle_observation(
        PartialTranscriptObservation(
            text="トモコ、今日の予定を教えて",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
            trace_id=partial.observation.trace_id,
        )
    )

    assert partial.speech_order is not None
    assert partial.speech_order.text == "うん、聞いてるよ。"
    assert partial.scheduler_output.reason == (
        "partial acknowledgement before complete request"
    )
    assert chat.calls == 1
    assert final.speech_order is not None
    assert final.speech_order.mode == SpeechOrderMode.REPLACE_CURRENT
    assert final.speech_order.text == "本回答を短く返すね。"


@pytest.mark.asyncio
async def test_tomoko_conversation_core_acknowledges_calendar_topic_without_pause_hint() -> None:
    now = utc_now()
    chat = CountingChatBackend(["本回答を短く返すね。"])
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=FixedSaturationJudge(0.60),
        scheduler=SpeechScheduler(),
        chat_backend=chat,
    )
    core.update_turn_materials(
        TurnMaterials(
            window_ms=200,
            user_speaking=True,
            speech_probability=1.0,
            p_yielding=0.29,
            silence_ms=0,
            playback_active=False,
            stt_partial="今日の予定",
        )
    )

    partial = await core.handle_observation(
        PartialTranscriptObservation(
            text="今日の予定",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert partial.speech_order is not None
    assert partial.speech_order.text == "うん、聞いてるよ。"
    assert partial.scheduler_output.reason == (
        "partial acknowledgement before complete request"
    )
    assert chat.calls == 0


@pytest.mark.asyncio
async def test_tomoko_conversation_core_acknowledges_short_calendar_topic() -> None:
    now = utc_now()
    chat = CountingChatBackend(["本回答を短く返すね。"])
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=FixedSaturationJudge(0.60),
        scheduler=SpeechScheduler(),
        chat_backend=chat,
    )
    core.update_turn_materials(
        TurnMaterials(
            window_ms=200,
            user_speaking=False,
            speech_probability=0.0,
            p_yielding=0.35,
            silence_ms=400,
            playback_active=False,
            stt_partial="会議",
        )
    )

    partial = await core.handle_observation(
        PartialTranscriptObservation(
            text="会議",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert partial.speech_order is not None
    assert partial.speech_order.text == "うん、聞いてるよ。"
    assert partial.scheduler_output.reason == (
        "partial acknowledgement before complete request"
    )
    assert chat.calls == 0


@pytest.mark.asyncio
async def test_tomoko_conversation_core_acknowledges_topic_after_start_gate() -> None:
    now = utc_now()
    chat = CountingChatBackend(["本回答を短く返すね。"])
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=FixedSaturationJudge(0.45),
        scheduler=SpeechScheduler(),
        chat_backend=chat,
        world_materials=WorldMaterials(
            external_result_importance=0.9,
            followup_importance=0.9,
        ),
    )
    core.update_turn_materials(
        TurnMaterials(
            window_ms=200,
            user_speaking=True,
            speech_probability=0.6,
            p_yielding=0.43,
            silence_ms=0,
            playback_active=False,
            stt_partial="今日の天気は",
        )
    )

    partial = await core.handle_observation(
        PartialTranscriptObservation(
            text="今日の天気は",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert partial.speech_order is not None
    assert partial.speech_order.text == "うん、聞いてるよ。"
    assert partial.scheduler_output.reason == (
        "partial acknowledgement before complete request"
    )
    assert chat.calls == 0


@pytest.mark.asyncio
async def test_tomoko_conversation_core_acknowledges_lunch_topic_variant() -> None:
    now = utc_now()
    chat = CountingChatBackend(["本回答を短く返すね。"])
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=FixedSaturationJudge(0.45),
        scheduler=SpeechScheduler(),
        chat_backend=chat,
        world_materials=WorldMaterials(
            external_result_importance=0.9,
            followup_importance=0.9,
        ),
    )
    core.update_turn_materials(
        TurnMaterials(
            window_ms=200,
            user_speaking=True,
            speech_probability=0.6,
            p_yielding=0.43,
            silence_ms=0,
            playback_active=False,
            stt_partial="お勧めの昼ご飯",
        )
    )

    partial = await core.handle_observation(
        PartialTranscriptObservation(
            text="お勧めの昼ご飯",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert partial.speech_order is not None
    assert partial.speech_order.text == "うん、聞いてるよ。"
    assert partial.scheduler_output.reason == (
        "partial acknowledgement before complete request"
    )
    assert chat.calls == 0


@pytest.mark.asyncio
async def test_tomoko_conversation_core_acknowledges_story_topic_fragment() -> None:
    now = utc_now()
    chat = CountingChatBackend(["本回答を短く返すね。"])
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=FixedSaturationJudge(0.45),
        scheduler=SpeechScheduler(),
        chat_backend=chat,
        world_materials=WorldMaterials(
            external_result_importance=0.9,
            followup_importance=0.9,
        ),
    )
    core.update_turn_materials(
        TurnMaterials(
            window_ms=200,
            user_speaking=True,
            speech_probability=0.6,
            p_yielding=0.43,
            silence_ms=0,
            playback_active=False,
            stt_partial="の話を",
        )
    )

    partial = await core.handle_observation(
        PartialTranscriptObservation(
            text="の話を",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert partial.speech_order is not None
    assert partial.speech_order.text == "うん、聞いてるよ。"
    assert partial.scheduler_output.reason == (
        "partial acknowledgement before complete request"
    )
    assert chat.calls == 0


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
            p_yielding=0.92,
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

    assert first_partial.speech_order is not None
    assert first_partial.speech_order.response_kind == ResponseKind.CONTENT
    assert partial.speech_order is None
    assert partial.scheduler_output.reason == "partial reconciled with active partial reply"
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
            trace_id=first_partial.observation.trace_id,
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
            p_yielding=0.92,
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
            p_yielding=0.92,
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

    assert first.speech_order is not None
    assert active.speech_order is None
    assert active.scheduler_output.reason == "partial reconciled with active partial reply"
    assert conflicting.speech_order is None
    assert conflicting.prompt_request is None
    assert conflicting.scheduler_output.reason == (
        "partial discarded after active partial reply in same trace"
    )


@pytest.mark.asyncio
async def test_tomoko_conversation_core_replaces_conflicting_final_after_partial_order() -> None:
    now = utc_now()
    trace_id = uuid4()
    chat = CountingChatBackend(["先に答えるね。", "ごめん、言い直すね。"])
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=FixedSaturationJudge(0.95),
        scheduler=SpeechScheduler(),
        chat_backend=chat,
    )

    active = await core.handle_observation(
        PartialTranscriptObservation(
            text="その今の予定を教えて",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
            trace_id=trace_id,
            p_yielding=0.92,
        )
    )
    reconciled_partial = await core.handle_observation(
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
            text="明日の会議の資料はどこにあるか教えて",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
            trace_id=trace_id,
        )
    )

    assert active.speech_order is not None
    assert reconciled_partial.speech_order is None
    assert reconciled_partial.scheduler_output.reason == (
        "partial reconciled with active partial reply"
    )
    assert final.durable_utterance is not None
    assert final.speech_order is not None
    assert final.speech_order.mode == SpeechOrderMode.REPLACE_CURRENT
    assert final.speech_order.text == "ごめん、言い直すね。"
    assert final.speech_order.response_kind == ResponseKind.CORRECTION
    assert final.scheduler_output.reason == (
        "final diverged from active partial reply; replacing"
    )

    stale_partial = await core.handle_observation(
        PartialTranscriptObservation(
            text="明日の会議の資料はどこにあるか教えて",
            is_final=False,
            stability=0.85,
            audio_started_at=now,
            audio_ended_at=now,
            trace_id=trace_id,
        )
    )
    assert stale_partial.speech_order is None
    assert stale_partial.prompt_request is None
    assert stale_partial.scheduler_output.reason == (
        "partial reconciled with active partial reply"
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
async def test_tomoko_conversation_core_includes_user_status_in_prompt_snapshot() -> None:
    now = utc_now()
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(["見えている状態も踏まえるね。"]),
    )
    core.update_user_status(
        UserStatusObservation(
            present=True,
            activity_label="coding_or_terminal",
            summary="coding_or_terminal: pytest failed in Codex terminal",
            source="unit",
            confidence=0.9,
        )
    )

    result = await core.handle_observation(
        PartialTranscriptObservation(
            text="今の作業を見て短く返して",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert result.context_snapshot.user_status is not None
    assert result.context_snapshot.user_status.activity_label == "coding_or_terminal"
    assert result.prompt_request is not None
    assert "user_status=coding_or_terminal" in result.prompt_request.prompt_text
    assert result.scheduler_output.score_breakdown[
        "pressure_world_user_presence"
    ] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_tomoko_conversation_core_includes_active_candidates_in_prompt_snapshot() -> None:
    now = utc_now()
    candidate = CandidateRecord(
        seed_id=uuid4(),
        source="world",
        source_key="rain-now",
        text="いま外は雨が降っている",
        priority=0.8,
        urgency=0.6,
        intrusion=0.1,
        maturity=1.0,
        lifecycle=CandidateLifecycle.ACTIVE,
        context_tags=("weather",),
        candidate_score=0.9,
    )
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(["雨のことも踏まえるね。"]),
        candidate_provider=lambda: [candidate],
    )

    result = await core.handle_observation(
        PartialTranscriptObservation(
            text="今どう思う",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert result.context_snapshot.candidates == (candidate,)
    assert result.prompt_request is not None
    assert "candidate[world:rain-now]=いま外は雨が降っている" in (
        result.prompt_request.prompt_text
    )
    assert result.scheduler_output.score_breakdown[
        "pressure_world_candidate_pressure"
    ] == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_tomoko_conversation_core_includes_updated_candidate_records() -> None:
    now = utc_now()
    candidate = CandidateRecord(
        seed_id=uuid4(),
        source="calendar",
        source_key="2026-07-04T10:00:00+09:00",
        text="10:00 健康診断",
        priority=0.8,
        urgency=0.7,
        intrusion=0.2,
        maturity=1.0,
        lifecycle=CandidateLifecycle.ACTIVE,
        context_tags=("calendar", "reminder"),
        candidate_score=0.78,
    )
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(["予定も踏まえるね。"]),
    )
    core.update_candidate_records([candidate])

    result = await core.handle_observation(
        PartialTranscriptObservation(
            text="今どうするのがよさそう",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert result.context_snapshot.candidates == (candidate,)
    assert result.prompt_request is not None
    assert "candidate[calendar:2026-07-04T10:00:00+09:00]=10:00 健康診断" in (
        result.prompt_request.prompt_text
    )
    assert result.scheduler_output.score_breakdown[
        "pressure_world_candidate_pressure"
    ] == pytest.approx(0.78)


@pytest.mark.asyncio
async def test_tomoko_conversation_core_initiative_tick_speaks_from_candidate() -> None:
    candidate = CandidateRecord(
        seed_id=uuid4(),
        source="world",
        source_key="rain-now",
        text="いま外は雨が降っている",
        priority=0.8,
        urgency=0.6,
        intrusion=0.1,
        maturity=1.0,
        lifecycle=CandidateLifecycle.ACTIVE,
        context_tags=("weather",),
        candidate_score=0.9,
    )
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(["そういえば、外は雨みたい。"]),
        candidate_provider=lambda: [candidate],
    )
    core.update_turn_materials(
        TurnMaterials(
            window_ms=200,
            user_speaking=False,
            speech_probability=0.0,
            p_yielding=None,
            silence_ms=3200,
            playback_active=False,
        )
    )

    result = await core.handle_initiative_tick()

    assert result.durable_utterance is None
    assert result.scheduler_output.text_intent == "initiative"
    assert result.scheduler_output.action == "replace_current"
    assert "candidate pressure initiative tick" in result.scheduler_output.reason
    assert result.context_snapshot is not None
    assert result.context_snapshot.candidates == (candidate,)
    assert result.prompt_request is not None
    assert result.prompt_request.scope == PromptScope.INITIATIVE
    assert result.prompt_request.candidate_id == candidate.id
    assert "candidate[world:rain-now]=いま外は雨が降っている" in (
        result.prompt_request.prompt_text
    )
    assert result.speech_order is not None
    assert result.speech_order.text == "そういえば、外は雨みたい。"
    assert result.scheduler_output.score_breakdown[
        "pressure_world_candidate_pressure"
    ] == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_tomoko_conversation_core_initiative_tick_suppresses_when_absent() -> None:
    candidate = CandidateRecord(
        seed_id=uuid4(),
        source="world",
        source_key="rain-now",
        text="いま外は雨が降っている",
        priority=0.8,
        urgency=0.6,
        intrusion=0.1,
        maturity=1.0,
        lifecycle=CandidateLifecycle.ACTIVE,
        context_tags=("weather",),
        candidate_score=0.9,
    )
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(["呼ばれないはず。"]),
        candidate_provider=lambda: [candidate],
    )
    core.update_user_status(
        UserStatusObservation(
            present=False,
            activity_label="away",
            summary="away: no user detected",
            source="unit",
            confidence=0.9,
        )
    )
    core.update_turn_materials(
        TurnMaterials(
            window_ms=200,
            user_speaking=False,
            speech_probability=0.0,
            p_yielding=None,
            silence_ms=3200,
            playback_active=False,
        )
    )

    result = await core.handle_initiative_tick()

    assert result.scheduler_output.text_intent == "initiative"
    assert result.scheduler_output.action == "suppress"
    assert result.scheduler_output.reason == "user absence suppresses initiative tick"
    assert result.prompt_request is None
    assert result.speech_order is None
    assert result.scheduler_output.score_breakdown[
        "pressure_world_user_absence"
    ] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_attention_mode_idle_suppresses_low_saturation_until_wake() -> None:
    now = utc_now()
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(["聞くね。", "戻ったよ。"]),
    )

    wake = await core.handle_observation(
        PartialTranscriptObservation(
            text="トモコ、聞いて",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )
    assert wake.speech_order is not None
    assert core.attention_mode == "conversation"
    core.update_playback_state(False)

    core.update_turn_materials(
        TurnMaterials(
            window_ms=200,
            user_speaking=False,
            speech_probability=0.0,
            p_yielding=None,
            silence_ms=10_000,
            playback_active=False,
        )
    )
    ambient = await core.handle_observation(
        PartialTranscriptObservation(
            text="今日は疲れた",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert core.attention_mode == "ambient"
    assert ambient.speech_order is None
    assert ambient.scheduler_output.action == "suppress"
    assert ambient.scheduler_output.reason == "ambient attention suppresses low-saturation speech"
    assert ambient.scheduler_output.score_breakdown["attention_mode_ambient"] == pytest.approx(
        1.0
    )

    rewake = await core.handle_observation(
        PartialTranscriptObservation(
            text="トモコ、戻って",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert core.attention_mode == "conversation"
    assert rewake.speech_order is not None
    assert rewake.speech_order.text


@pytest.mark.asyncio
async def test_attention_mode_stop_intent_returns_to_ambient() -> None:
    now = utc_now()
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=SemanticSaturationJudge(),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(["聞くね。"]),
    )

    await core.handle_observation(
        PartialTranscriptObservation(
            text="トモコ、聞いて",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )
    stop = await core.handle_observation(
        PartialTranscriptObservation(
            text="もういいよ",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert stop.speech_order is not None
    assert stop.speech_order.mode == SpeechOrderMode.STOP
    assert core.attention_mode == "ambient"
    assert stop.scheduler_output.score_breakdown["attention_mode_ambient"] == pytest.approx(
        1.0
    )


@pytest.mark.asyncio
async def test_attention_mode_ambient_allows_request_like_question() -> None:
    now = utc_now()
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=FixedSaturationJudge(0.55),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(["天気を見てみるね。"]),
    )
    core.attention_mode = "ambient"
    core.update_turn_materials(
        TurnMaterials(
            window_ms=200,
            user_speaking=False,
            speech_probability=0.0,
            p_yielding=0.3,
            silence_ms=10000,
            playback_active=False,
        )
    )

    result = await core.handle_observation(
        PartialTranscriptObservation(
            text="今日の天気はどうなりそう",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert result.speech_order is not None
    assert result.speech_order.text == "天気を見てみるね。"
    assert core.attention_mode == "conversation"


@pytest.mark.asyncio
async def test_attention_mode_ambient_allows_task_list_request() -> None:
    now = utc_now()
    core = TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=FixedSaturationJudge(0.55),
        scheduler=SpeechScheduler(),
        chat_backend=StaticChatBackend(["三つに絞るね。"]),
    )
    core.attention_mode = "ambient"
    core.update_turn_materials(
        TurnMaterials(
            window_ms=200,
            user_speaking=False,
            speech_probability=0.0,
            p_yielding=0.3,
            silence_ms=10000,
            playback_active=False,
        )
    )

    result = await core.handle_observation(
        PartialTranscriptObservation(
            text="今日やるべきことを3つ挙げて",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert result.speech_order is not None
    assert result.speech_order.text == "三つに絞るね。"
    assert core.attention_mode == "conversation"


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
        response_kind=ResponseKind.CONTENT,
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
        response_kind=ResponseKind.CONTENT,
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
async def test_speech_order_executor_streams_chunks_before_returning() -> None:
    executor = SpeechOrderExecutor(
        StaticWavTtsBackend([b"RIFF1111WAVEdata", b"RIFF2222WAVEdata"])
    )
    order = SpeechOrder(
        text="先に流す",
        mode=SpeechOrderMode.REPLACE_CURRENT,
        reason="unit",
        priority=50,
        response_kind=ResponseKind.CONTENT,
    )
    streamed: list[bytes] = []

    async def on_chunk(chunk) -> None:
        streamed.append(chunk.chunk)

    result = await executor.execute_stream(order, on_chunk=on_chunk)

    assert streamed == [b"RIFF1111WAVEdata", b"RIFF2222WAVEdata"]
    assert [chunk.chunk for chunk in result.audio_chunks] == streamed
    assert result.audio_chunks[-1].is_final


@pytest.mark.asyncio
async def test_speech_order_executor_splits_multi_sentence_text_for_tts() -> None:
    class RecordingTtsBackend:
        def __init__(self) -> None:
            self.texts: list[str] = []
            self.chunks = [b"RIFF1111WAVEdata", b"RIFF2222WAVEdata"]

        async def synthesize_chunks(self, _request, text: str):
            self.texts.append(text)
            yield self.chunks[len(self.texts) - 1]

    backend = RecordingTtsBackend()
    executor = SpeechOrderExecutor(backend)
    order = SpeechOrder(
        text="まず一言。続きも話すね。",
        mode=SpeechOrderMode.REPLACE_CURRENT,
        reason="unit",
        priority=50,
        response_kind=ResponseKind.CONTENT,
    )
    streamed: list[bytes] = []

    async def on_chunk(chunk) -> None:
        streamed.append(chunk.chunk)

    result = await executor.execute_stream(order, on_chunk=on_chunk)

    assert backend.texts == ["まず一言。", "続きも話すね。"]
    assert streamed == [b"RIFF1111WAVEdata", b"RIFF2222WAVEdata"]
    assert [chunk.chunk for chunk in result.audio_chunks] == streamed


@pytest.mark.asyncio
async def test_speech_order_executor_splits_long_clause_at_reading_pause_for_tts() -> None:
    class RecordingTtsBackend:
        def __init__(self) -> None:
            self.texts: list[str] = []
            self.chunks = [b"RIFF1111WAVEdata", b"RIFF2222WAVEdata"]

        async def synthesize_chunks(self, _request, text: str):
            self.texts.append(text)
            yield self.chunks[len(self.texts) - 1]

    backend = RecordingTtsBackend()
    executor = SpeechOrderExecutor(backend)
    order = SpeechOrder(
        text="うーん、予報だと午後は少し雲が広がりそうだけど、一日中晴れってわけでもなさそうだね。",
        mode=SpeechOrderMode.REPLACE_CURRENT,
        reason="unit",
        priority=50,
        response_kind=ResponseKind.CONTENT,
    )
    streamed: list[bytes] = []

    async def on_chunk(chunk) -> None:
        streamed.append(chunk.chunk)

    await executor.execute_stream(order, on_chunk=on_chunk)

    assert backend.texts == [
        "うーん、予報だと午後は少し雲が広がりそうだけど、",
        "一日中晴れってわけでもなさそうだね。",
    ]
    assert streamed == [b"RIFF1111WAVEdata", b"RIFF2222WAVEdata"]


@pytest.mark.asyncio
async def test_speech_order_executor_stop_playback_clears_queue_and_generation() -> None:
    executor = SpeechOrderExecutor(StaticWavTtsBackend([b"RIFFxxxxWAVEdata"]))
    current = SpeechOrder(
        text="話している途中",
        mode=SpeechOrderMode.REPLACE_CURRENT,
        reason="unit",
        priority=50,
        response_kind=ResponseKind.CONTENT,
    )
    queued = SpeechOrder(
        text="次に話す",
        mode=SpeechOrderMode.APPEND_AFTER_CURRENT,
        reason="unit",
        priority=40,
        response_kind=ResponseKind.CONTENT,
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
        response_kind=ResponseKind.CONTENT,
    )
    executor.begin_external_playback(current, score=1.0)

    final_replace = SpeechOrder(
        text="final reply",
        mode=SpeechOrderMode.REPLACE_CURRENT,
        reason="final",
        priority=100,
        response_kind=ResponseKind.CONTENT,
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
