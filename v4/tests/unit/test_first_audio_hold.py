"""MOCK: 実際の BaselineSession/VadSensor の初回音声だけを保留する回帰試験。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace

import numpy as np
import pytest
from first_audio_hold_support import HoldRig, drain, quiet, voice

from engine import baseline
from engine.sensors.vad import WINDOW
from scripts.diagnose_reply_race import _ClockLoop

pytestmark = pytest.mark.unit


@pytest.fixture
async def hold_rig(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator:
    clock = _ClockLoop(asyncio.get_running_loop())
    facade = SimpleNamespace(**vars(asyncio))
    facade.get_running_loop = lambda: clock
    monkeypatch.setattr(baseline, "asyncio", facade)
    rigs: list[HoldRig] = []

    def make(**options: object) -> HoldRig:
        options.setdefault("barge_in", True)
        rig = HoldRig(clock, **options)
        rigs.append(rig)
        return rig

    try:
        yield make
    finally:
        for rig in rigs:
            await rig.session.close()
        await drain()


@pytest.mark.parametrize("streaming", [False, True])
async def test_first_audio_waits_then_sustained_resume_discards_and_joins(
    hold_rig, streaming: bool,
) -> None:
    rig = hold_rig(streaming=streaming)
    await rig.prepare_reply()
    await rig.feed(voice(4))
    await rig.ready_audio()
    assert rig.events("tts_first_audio")
    assert not rig.sent
    assert not rig.session._audible
    assert not rig.session._spoken
    assert rig.session._play_end == 0
    assert rig.maai.tomoko == []
    assert len(rig.events("reply_audio_hold")) == 1
    await rig.feed(voice(12))
    assert not rig.sent
    assert rig.events("discard_reply")
    assert rig.events("reply_audio_cancel")
    if streaming:
        assert rig.stream_closed.is_set()
    await rig.feed(quiet(12))
    assert {packet["generation"] for packet in rig.sent} == {1}
    assert len(rig.heard) == 2
    assert len(rig.heard[1]) > len(rig.heard[0])
    assert not rig.events("stop")


@pytest.mark.parametrize("windows,closing", [(8, "vad_end"), (4, "vad_drop")])
async def test_short_end_or_drop_releases_original_reply_once(
    hold_rig, windows: int, closing: str,
) -> None:
    rig = hold_rig()
    await rig.prepare_reply()
    await rig.feed(voice(windows))
    await rig.ready_audio()
    assert not rig.sent
    await rig.feed(quiet(12))
    assert rig.events(closing)
    assert rig.sent and {packet["generation"] for packet in rig.sent} == {0}
    assert len(rig.heard) == len(rig.prompts) == 1
    assert rig.session._queue.empty()
    assert len(rig.events("reply_audio_release")) == 1
    sent = rig.events("reply_first_audio_send")
    assert len(sent) == 1
    assert sent[0]["turn"] == 1 and sent[0]["generation"] == 0
    assert sent[0]["user_speaking"] is False and sent[0]["sustained"] is False
    assert "vad_now_s" in sent[0]


async def test_short_voice_before_tts_is_ready_does_not_queue_duplicate_reply(hold_rig) -> None:
    rig = hold_rig()
    await rig.prepare_reply()
    await rig.feed(voice(8))
    await rig.feed(quiet(12))
    await rig.ready_audio()
    assert len(rig.heard) == len(rig.prompts) == 1
    assert {packet["generation"] for packet in rig.sent} == {0}


async def test_standalone_short_request_still_gets_an_answer(hold_rig) -> None:
    rig = hold_rig()
    rig.tts_gate.set()
    await rig.feed(voice(8))
    await rig.feed(quiet(12))
    assert rig.sent and len(rig.heard) == len(rig.prompts) == 1
    assert not rig.events("reply_audio_hold")


async def test_end_then_new_start_before_waiter_resumes_does_not_release_audio(hold_rig) -> None:
    rig = hold_rig()
    await rig.prepare_reply()
    await rig.feed(voice(4))
    await rig.ready_audio()
    rig.feed_batch(np.concatenate([quiet(12), voice(4)]))
    await drain()
    assert not rig.sent
    await rig.feed(quiet(12))
    assert rig.sent and len(rig.heard) == 1
    assert len(rig.events("reply_first_audio_send")) == 1


async def test_resume_start_before_answer_task_is_created_still_holds_audio(hold_rig) -> None:
    rig = hold_rig()
    rig.feed_batch(np.concatenate([voice(32), quiet(12), voice(4)]))
    assert rig.session._answering is None
    await rig.ready_audio()
    assert rig.events("tts_first_audio") and not rig.sent
    await rig.feed(quiet(12))
    assert rig.sent and len(rig.heard) == len(rig.prompts) == 1


async def test_batched_sustained_resume_coalesces_queued_original_before_worker_runs(hold_rig) -> None:
    rig = hold_rig()
    rig.feed_batch(np.concatenate([voice(32, 0.3), quiet(12), voice(16, 0.7)]))
    assert rig.session._answering is None
    await rig.ready_audio()
    assert not rig.sent
    await rig.feed(quiet(12))
    assert len(rig.heard) == len(rig.prompts) == 1
    assert np.count_nonzero(rig.heard[0] == np.float32(0.3)) == 32 * WINDOW
    assert np.count_nonzero(rig.heard[0] == np.float32(0.7)) == 16 * WINDOW
    assert len(rig.events("reply_first_audio_send")) == 1


async def test_already_cancelling_answer_does_not_duplicate_original_across_batched_voices(
    hold_rig,
) -> None:
    rig = hold_rig()
    await rig.prepare_reply()
    rig.feed_batch(np.concatenate([
        voice(16, 0.5), quiet(12), voice(16, 0.7), quiet(12)]))
    await rig.ready_audio()
    assert len(rig.heard) == 2
    joined = rig.heard[1]
    for windows, level in [(32, 0.3), (16, 0.5), (16, 0.7)]:
        assert np.count_nonzero(joined == np.float32(level)) == windows * WINDOW
    assert {packet["generation"] for packet in rig.sent} == {1}
    assert len(rig.events("discard_reply")) == 1


@pytest.mark.parametrize("streaming", [False, True])
async def test_close_cancels_held_first_audio_without_releasing_it(
    hold_rig, streaming: bool,
) -> None:
    rig = hold_rig(streaming=streaming)
    await rig.prepare_reply()
    await rig.feed(voice(4))
    await rig.ready_audio()
    assert not rig.sent
    await asyncio.wait_for(rig.session.close(), timeout=1.0)
    if streaming:
        assert rig.stream_closed.is_set()
    await drain()
    assert rig.session._answering.done()
    assert not rig.sent
    assert rig.events("reply_audio_cancel")
    assert not rig.events("reply_audio_release")


async def test_already_audible_stream_continues_during_short_voice(hold_rig) -> None:
    rig = hold_rig(streaming=True)
    rig.stream_tail_gate.clear()
    await rig.prepare_reply()
    await rig.ready_audio()
    assert rig.sent
    count = len(rig.sent)
    await rig.feed(voice(4))
    rig.stream_tail_gate.set()
    await drain()
    assert len(rig.sent) > count
    assert not rig.events("reply_audio_hold")
    assert len(rig.events("reply_first_audio_send")) == 1


async def test_later_phrase_is_not_gated_again_after_first_main_send(hold_rig) -> None:
    rig = hold_rig(phrases=2)
    rig.later_gate.clear()
    await rig.prepare_reply()
    await rig.ready_audio()
    count = len(rig.sent)
    assert count
    await rig.feed(voice(4))
    rig.later_gate.set()
    await drain()
    assert len(rig.sent) > count
    assert not rig.events("reply_audio_hold")
    assert len(rig.events("reply_first_audio_send")) == 1


async def test_explicit_backchannel_does_not_wait_for_main_reply_gate(hold_rig) -> None:
    rig = hold_rig(backchannel=True)
    await rig.feed(voice(40))
    rig.frame(p_bc=0.9)
    await rig.feed(quiet(2))
    assert rig.sent and rig.sent[0]["samples"] == 7200
    assert not rig.session._audible
    assert not rig.events("reply_first_audio_send")
    assert not rig.events("reply_audio_hold")


async def test_barge_in_false_keeps_sending_and_queues_short_new_utterance(hold_rig) -> None:
    rig = hold_rig(barge_in=False)
    await rig.prepare_reply()
    await rig.feed(voice(8))
    await rig.ready_audio()
    assert rig.sent and rig.sent[0]["speaking"] is True
    await rig.feed(quiet(12))
    assert len(rig.heard) == len(rig.prompts) == 2
    assert not rig.events("reply_audio_hold")


async def test_no_resumption_adds_no_wait_between_tts_ready_and_first_send(hold_rig) -> None:
    rig = hold_rig()
    await rig.prepare_reply()
    await rig.ready_audio()
    ready = rig.events("tts_first_audio")[0]
    sent = rig.events("reply_first_audio_send")[0]
    assert ready["mic_s"] == sent["mic_s"]
    assert not rig.events("reply_audio_hold")


@pytest.mark.parametrize("failure", [False, True])
async def test_short_voice_answers_when_original_stt_disappeared_after_start(
    hold_rig, failure: bool,
) -> None:
    rig = hold_rig()

    async def transcribe(audio: np.ndarray) -> str:
        text = await rig.transcribe(audio)
        if len(rig.heard) == 1:
            await rig.tts_gate.wait()
            if failure:
                raise RuntimeError("MOCK: first STT failed")
            return ""
        return text

    rig.session._transcribe = transcribe
    await rig.feed(np.concatenate([voice(32), quiet(12)]))
    await rig.feed(voice(8))
    await rig.ready_audio()
    assert not rig.session._answer_in_flight()
    await rig.feed(quiet(12))
    assert len(rig.heard) == 2 and len(rig.prompts) == 1 and rig.sent
    assert not rig.events("ignored_pending_short_voice")


async def test_short_voice_answers_when_original_tts_disappeared_after_start(hold_rig) -> None:
    rig = hold_rig()

    async def synthesize(phrase: str) -> np.ndarray:
        audio = await rig.synthesize(phrase)
        if rig.tts_calls == 1:
            raise RuntimeError("MOCK: first TTS failed")
        return audio

    rig.session._synthesize = synthesize
    await rig.prepare_reply()
    await rig.feed(voice(8))
    await rig.ready_audio()
    assert not rig.session._answer_in_flight()
    await rig.feed(quiet(12))
    assert len(rig.heard) == len(rig.prompts) == 2 and rig.sent
    assert not rig.events("ignored_pending_short_voice")
