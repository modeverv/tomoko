import asyncio

import numpy as np
import pytest

from engine.baseline import BaselineSession
from engine.dto import EngineConfig
from engine.front_llm import PhraseSplitter
from engine.sensors.vad import WINDOW, VadSensor
from engine.trace import Trace

pytestmark = pytest.mark.unit


def loud(n):
    return np.full(WINDOW * n, 0.5, dtype=np.float32)


def quiet(n):
    return np.zeros(WINDOW * n, dtype=np.float32)


def by_level(window):
    return 1.0 if float(np.max(np.abs(window))) > 0.1 else 0.0


def make_vad(**kw):
    return VadSensor(by_level, silence_s=0.384, preroll_s=0.064, min_voiced_s=0.128, **kw)


# ---- VAD ----

def test_vad_cuts_an_utterance_after_the_silence_and_keeps_the_preroll():
    vad = make_vad()
    events = vad.feed(np.concatenate([quiet(10), loud(20), quiet(12)]))
    assert [e.kind for e in events] == ["start", "end"]
    start, end = events
    assert start.at_s == pytest.approx(10 * 0.032)
    assert end.utterance.end_s == pytest.approx(30 * 0.032)
    assert len(end.utterance.audio) == WINDOW * 22  # 手前 2 窓 + 声 20 窓。後ろの無音は含まない
    assert not vad.speaking


def test_vad_does_not_cut_on_a_short_pause():
    vad = make_vad()
    events = vad.feed(np.concatenate([loud(10), quiet(8), loud(10), quiet(12)]))
    assert [e.kind for e in events] == ["start", "end"]
    assert events[1].utterance.end_s == pytest.approx(28 * 0.032)


def test_vad_drops_a_click():
    vad = make_vad()
    events = vad.feed(np.concatenate([quiet(5), loud(2), quiet(12)]))
    assert [e.kind for e in events] == ["start", "drop"]


def test_vad_accepts_any_frame_size():
    whole = np.concatenate([quiet(10), loud(20), quiet(12)])
    a = make_vad().feed(whole)
    vad, b = make_vad(), []
    for i in range(0, len(whole), 128):
        b += vad.feed(whole[i : i + 128])
    assert [(e.kind, e.at_s) for e in a] == [(e.kind, e.at_s) for e in b]


# ---- 句の切り出し ----

def test_phrases_are_cut_at_sentence_ends():
    s = PhraseSplitter()
    assert s.feed("そうだね、パン") == []
    assert s.feed("がいいよ。あとは") == ["そうだね、パンがいいよ。"]
    assert s.feed("卵！？") == ["あとは卵！", ]
    assert s.flush() == []


def test_phrase_flush_returns_the_rest():
    s = PhraseSplitter()
    s.feed("はい。たぶん")
    assert s.flush() == ["たぶん"]


# ---- 通しの経路 ----

async def test_session_answers_utterances_in_order_without_blocking_the_mic():
    sent, texts = [], []
    release = asyncio.Event()

    async def transcribe(audio):
        return f"発話{len(audio) // WINDOW}"

    async def reply(messages):
        await release.wait()  # LLM が遅くても、マイクの受け取りは止まらない
        yield f"{messages[-1]['content']}への"
        yield "返事。次の文。"

    async def synthesize(phrase):
        return np.full(2400, 0.1, dtype=np.float32)

    async def send_audio(generation, seq, samples):
        sent.append((generation, seq, len(samples)))

    async def send_json(obj):
        texts.append((obj["role"], obj["text"]))

    session = BaselineSession(EngineConfig(send_chunk_s=0.05), make_vad(), Trace(None),
                              transcribe, reply, synthesize, send_audio, send_json)
    session.on_mic(np.concatenate([loud(10), quiet(12)]))
    session.on_mic(np.concatenate([loud(20), quiet(12)]))  # 1 つ目に答える前に 2 つ目が来る
    release.set()
    for _ in range(50):
        await asyncio.sleep(0.01)
        if len(texts) == 4:
            break
    await session.close()
    assert texts == [("user", "発話10"), ("tomoko", "発話10への返事。次の文。"),
                     ("user", "発話20"), ("tomoko", "発話20への返事。次の文。")]
    # 1 つの返事は 1 つの世代。句 2 つ × 2 断片が、通し番号の順に出る
    assert sent == [(0, 0, 1200), (0, 1, 1200), (0, 2, 1200), (0, 3, 1200),
                    (1, 0, 1200), (1, 1, 1200), (1, 2, 1200), (1, 3, 1200)]


async def test_a_failed_turn_does_not_stop_the_conversation():
    texts = []

    async def transcribe(audio):
        if len(audio) // WINDOW == 10:
            raise RuntimeError("boom")
        return "二つ目"

    async def reply(messages):
        yield "はい。"

    async def synthesize(phrase):
        return np.zeros(10, dtype=np.float32)

    async def nothing(*a):
        pass

    async def send_json(obj):
        texts.append(obj["text"])

    session = BaselineSession(EngineConfig(), make_vad(), Trace(None), transcribe, reply,
                              synthesize, nothing, send_json)
    session.on_mic(np.concatenate([loud(10), quiet(12), loud(20), quiet(12)]))
    await asyncio.sleep(0.05)
    await session.close()
    assert texts == ["二つ目", "はい。"]
