"""O0A-02: origin trace と generation owner の固定.

`decision_generation_id` は tomoko-process (`TomokoConversationCore`) だけが発行する
monotonic な世代番号で、Tomoko の判断サイクルを識別する。`playback_generation_id`
は hot-path (`SpeechOrderExecutor`) だけが発行する別の monotonic 世代番号で、
TTS/audio 実行の世代を識別する。両者は別々の owner が別々のカウンタで管理し、
互いの値を書き換えない。

`trace_id` は、STT observation から SpeechOrder 生成・hot-path 実行・
AudioChunkOut に至るまで、同じ causal lineage を指す既存 field である。この
field が openai.plan.md の `origin_trace_id` と同じ意味を持つため、新しい field
は追加せず `trace_id` をそのまま再利用する。
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from server.hot_path.model_executor import StaticWavTtsBackend
from server.hot_path.speech_executor import SpeechOrderExecutor
from server.shared.models import (
    PartialTranscriptObservation,
    ResponseKind,
    SemanticSaturationResult,
    SpeechOrder,
    SpeechOrderMode,
    utc_now,
)
from server.tomoko.conversation import TomokoConversationCore
from server.tomoko.scheduler import SpeechScheduler
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

    async def stream(self, request):
        self.calls += 1
        text = self.replies.pop(0) if self.replies else "fallback"
        yield text


def _core(replies: list[str], *, saturation: float = 0.95) -> TomokoConversationCore:
    return TomokoConversationCore(
        session_model=SessionBoundaryModel(),
        saturation_judge=FixedSaturationJudge(saturation),
        scheduler=SpeechScheduler(),
        chat_backend=CountingChatBackend(replies),
    )


@pytest.mark.asyncio
async def test_tomoko_conversation_core_issues_decision_generation_id() -> None:
    """decision_generation_id は tomoko-process だけが発行する（要件1）。"""
    now = utc_now()
    core = _core(["最初の返事だよ。"])

    result = await core.handle_observation(
        PartialTranscriptObservation(
            text="トモコ、今日の予定を教えて",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert result.speech_order is not None
    assert result.speech_order.decision_generation_id is not None
    assert isinstance(result.speech_order.decision_generation_id, int)
    assert result.speech_order.decision_generation_id >= 1


@pytest.mark.asyncio
async def test_hot_path_executor_never_mutates_decision_generation_id() -> None:
    """hot-path は受信した decision_generation_id を変更しない（要件2）。"""
    executor = SpeechOrderExecutor(StaticWavTtsBackend([b"RIFFxxxxWAVEdata"]))
    order = SpeechOrder(
        text="hot-path はここを触らない",
        mode=SpeechOrderMode.REPLACE_CURRENT,
        reason="unit",
        priority=50,
        response_kind=ResponseKind.CONTENT,
        decision_generation_id=42,
    )

    await executor.execute(order)

    assert order.decision_generation_id == 42


@pytest.mark.asyncio
async def test_speech_order_executor_issues_playback_generation_id_on_accept() -> None:
    """hot-path が order accept 時に playback_generation_id を発行する（要件3）。"""
    executor = SpeechOrderExecutor(StaticWavTtsBackend([b"RIFFxxxxWAVEdata"]))
    order = SpeechOrder(
        text="流すよ",
        mode=SpeechOrderMode.REPLACE_CURRENT,
        reason="unit",
        priority=50,
        response_kind=ResponseKind.CONTENT,
    )

    result = await executor.execute(order)

    assert result.playback_generation_id is not None
    assert isinstance(result.playback_generation_id, int)


def test_speech_order_dto_never_carries_playback_generation_id() -> None:
    """Tomoko は playback_generation_id を発行しない（要件4）。

    playback_generation_id は hot-path 所有の `SpeechOrderExecutionResult` の
    field としてのみ存在し、Tomoko が構築する `SpeechOrder` DTO には存在しない
    ことを構造的に固定する。
    """
    from dataclasses import fields

    field_names = {f.name for f in fields(SpeechOrder)}
    assert "playback_generation_id" not in field_names
    assert "decision_generation_id" in field_names


@pytest.mark.asyncio
async def test_trace_id_is_reused_as_origin_trace_id_across_order_and_execution() -> None:
    """既存 trace_id を origin_trace_id として再利用する（要件5・6）。

    openai.plan.md 4.2 の `origin_trace_id` は、STT observation が最初に
    作成する causal identity である。本コードベースの `trace_id` は既に
    observation -> SpeechOrder -> AudioChunkOut まで同じ値のまま伝播しており、
    意味が同一であるため、新しい field を追加せず `trace_id` を
    origin_trace_id として再利用する。
    """
    now = utc_now()
    trace_id = uuid4()
    core = _core(["観測のtrace_idを受け継ぐよ。"])

    result = await core.handle_observation(
        PartialTranscriptObservation(
            text="トモコ、今の予定を教えて",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
            trace_id=trace_id,
        )
    )

    assert result.speech_order is not None
    assert result.speech_order.trace_id == trace_id

    executor = SpeechOrderExecutor(StaticWavTtsBackend([b"RIFFxxxxWAVEdata"]))
    execution = await executor.execute(result.speech_order)

    assert execution.audio_chunks
    assert all(chunk.trace_id == trace_id for chunk in execution.audio_chunks)


@pytest.mark.asyncio
async def test_decision_generation_and_playback_generation_advance_independently() -> None:
    """replace 後に decision generation と playback generation が独立して進む（要件7）。"""
    now = utc_now()
    core = _core(["最初の返事だよ。", "二回目の返事だよ。"])

    first = await core.handle_observation(
        PartialTranscriptObservation(
            text="トモコ、今日の予定を教えて",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )
    # 発話が終わったことにして、2件目が append ではなく replace になるようにする。
    core.update_playback_state(False)
    second = await core.handle_observation(
        PartialTranscriptObservation(
            text="トモコ、明日の天気を教えて",
            is_final=True,
            stability=1.0,
            audio_started_at=now,
            audio_ended_at=now,
        )
    )

    assert first.speech_order is not None
    assert second.speech_order is not None
    assert second.speech_order.decision_generation_id == (
        first.speech_order.decision_generation_id + 1
    )

    executor = SpeechOrderExecutor(StaticWavTtsBackend([b"RIFFxxxxWAVEdata"]))
    first_execution = await executor.execute(first.speech_order)
    second_execution = await executor.execute(second.speech_order)

    assert second_execution.playback_generation_id == (
        first_execution.playback_generation_id + 1
    )
    # 二つの世代カウンタは別 owner が別々に管理しており、値が一致している
    # 必要はない（decision_generation_id はセッション内で increment、
    # playback_generation_id は replace/stop のたびに increment するため、
    # 呼び出し回数が異なれば値も乖離しうる）。ここでは「どちらも独立に
    # 単調増加すること」だけを固定する。
    assert first_execution.playback_generation_id is not None
    assert second_execution.playback_generation_id is not None
