import asyncio

import numpy as np
import pytest

from engine.baseline import BaselineSession
from engine.dto import EngineConfig
from engine.sensors.vad import WINDOW, VadSensor
from engine.trace import Trace

pytestmark = pytest.mark.unit


def loud(n):
    return np.full(WINDOW * n, 0.5, dtype=np.float32)


def quiet(n):
    return np.zeros(WINDOW * n, dtype=np.float32)


def by_level(window):
    return 1.0 if float(np.max(np.abs(window))) > 0.1 else 0.0


def make_vad():
    # 声が 8 窓（0.256 秒）続いたら、割り込みとみなす
    return VadSensor(by_level, silence_s=0.384, preroll_s=0.064, min_voiced_s=0.128, sustain_s=0.256)


def test_vad_reports_once_when_the_voice_has_lasted_long_enough():
    events = make_vad().feed(np.concatenate([quiet(3), loud(20), quiet(12)]))
    assert [e.kind for e in events] == ["start", "sustained", "end"]
    assert events[1].at_s == pytest.approx((3 + 8) * 0.032)


def test_vad_does_not_report_a_short_voice_as_sustained():
    events = make_vad().feed(np.concatenate([loud(5), quiet(12)]))
    assert [e.kind for e in events] == ["start", "end"]


class Rig:
    """部品を偽物にした会話。LLM と TTS の進み方を、試験から決める。"""

    def __init__(self, barge_in=True, audio_s=1.0):
        self.sent, self.messages, self.heard_audio, self.prompts = [], [], [], []
        self.llm_gate = asyncio.Event()
        self.llm_gate.set()
        self.audio_s = audio_s
        self.session = BaselineSession(
            EngineConfig(barge_in=barge_in, send_chunk_s=1.0), make_vad(), Trace(None),
            self.transcribe, self.reply, self.synthesize, self.send_audio, self.send_json)

    async def transcribe(self, audio):
        self.heard_audio.append(len(audio) // WINDOW)
        return f"発話{len(audio) // WINDOW}"

    async def reply(self, messages):
        self.prompts.append([m["content"] for m in messages])
        await self.llm_gate.wait()
        yield "一つ目。"
        await asyncio.sleep(0.02)
        yield "二つ目。"

    async def synthesize(self, phrase):
        return np.full(round(24_000 * self.audio_s), 0.1, dtype=np.float32)

    async def send_audio(self, generation, seq, samples):
        self.sent.append((generation, seq))

    async def send_json(self, obj):
        self.messages.append(obj)

    async def settle(self, until, tries=100):
        for _ in range(tries):
            if until():
                return
            await asyncio.sleep(0.005)
        raise AssertionError("待っても条件が成り立たなかった")

    def texts(self, role):
        return [m["text"] for m in self.messages if m.get("type") == "text" and m["role"] == role]


async def test_reply_not_yet_spoken_is_discarded_when_the_user_goes_on():
    rig = Rig()
    rig.llm_gate.clear()  # 返事を作っている最中で止めておく
    rig.session.on_mic(np.concatenate([loud(10), quiet(12)]))
    await rig.settle(lambda: len(rig.prompts) == 1)
    rig.session.on_mic(np.concatenate([quiet(5), loud(10), quiet(12)]))  # 言いよどみのあと、続きを話した
    rig.llm_gate.set()
    await rig.settle(lambda: len(rig.texts("tomoko")) == 1)
    await rig.session.close()
    # 1 回目の返事は声にならず、2 つの発話をつないだもの（声 10 + 間 + 声 10 窓）に 1 回だけ答える
    assert rig.heard_audio[0] == 10 and rig.heard_audio[1] > 20
    assert rig.prompts[1] == [f"発話{rig.heard_audio[1]}"]  # 捨てた回の発話は、記録に残さない
    assert {g for g, _ in rig.sent} == {1}
    assert not [m for m in rig.messages if m.get("type") == "stop"]


async def test_reply_being_spoken_stops_when_the_user_barges_in():
    rig = Rig(audio_s=5.0)
    rig.session.on_mic(np.concatenate([loud(10), quiet(12)]))
    await rig.settle(lambda: len(rig.sent) >= 1)  # 1 つ目の句の声が出た
    rig.session.on_mic(np.concatenate([loud(10), quiet(12)]))  # 声が鳴っている最中に割り込む
    await rig.settle(lambda: len(rig.texts("tomoko")) == 2)
    await rig.session.close()
    stops = [m for m in rig.messages if m.get("type") == "stop"]
    assert stops == [{"type": "stop", "generation": 0}]
    assert rig.texts("tomoko")[0] == "一つ目。"  # 声にした分だけを、話した記録にする
    # 割り込んだ発話には、新しい世代で答える。その LLM には、止めた返事の話した分が渡る
    assert rig.prompts[1][:2] == ["発話10", "一つ目。"]
    assert {g for g, _ in rig.sent} == {0, 1}


async def test_short_voice_while_speaking_is_neither_a_stop_nor_a_turn():
    rig = Rig(audio_s=5.0)
    rig.session.on_mic(np.concatenate([loud(10), quiet(12)]))
    await rig.settle(lambda: len(rig.texts("tomoko")) == 1)  # 返事は作り終え、声はまだ鳴っている
    rig.session.on_mic(np.concatenate([loud(5), quiet(12)]))  # 「うん」
    await asyncio.sleep(0.05)
    await rig.session.close()
    assert not [m for m in rig.messages if m.get("type") == "stop"]
    assert len(rig.prompts) == 1 and rig.texts("user") == ["発話10"]


async def test_stop_also_works_after_the_reply_was_fully_generated():
    rig = Rig(audio_s=5.0)
    rig.session.on_mic(np.concatenate([loud(10), quiet(12)]))
    await rig.settle(lambda: len(rig.texts("tomoko")) == 1)
    rig.session.on_mic(np.concatenate([loud(10), quiet(12)]))
    await rig.settle(lambda: len(rig.texts("tomoko")) == 2)
    await rig.session.close()
    assert [m for m in rig.messages if m.get("type") == "stop"] == [{"type": "stop", "generation": 0}]


async def test_without_barge_in_nothing_stops():
    rig = Rig(barge_in=False, audio_s=5.0)
    rig.session.on_mic(np.concatenate([loud(10), quiet(12)]))
    await rig.settle(lambda: len(rig.sent) >= 1)
    rig.session.on_mic(np.concatenate([loud(10), quiet(12)]))
    await rig.settle(lambda: len(rig.texts("tomoko")) == 2)
    await rig.session.close()
    assert not [m for m in rig.messages if m.get("type") == "stop"]
    assert rig.texts("tomoko") == ["一つ目。二つ目。", "一つ目。二つ目。"]


async def test_streamed_voice_is_sent_piece_by_piece_before_the_phrase_is_complete():
    rig, gate = Rig(barge_in=False), asyncio.Event()

    async def stream(phrase):
        yield np.full(24_000, 0.1, dtype=np.float32)
        await gate.wait()
        yield np.full(24_000, 0.1, dtype=np.float32)

    rig.session._synthesize_stream = stream
    rig.session.on_mic(np.concatenate([loud(10), quiet(13)]))
    await rig.settle(lambda: len(rig.sent) == 1)
    assert rig.sent == [(0, 0)]  # 句の残りを待たずに、最初の断片が出ている
    gate.set()
    await rig.settle(lambda: len(rig.sent) == 4)
    assert rig.sent == [(0, 0), (0, 1), (0, 2), (0, 3)]
    await rig.session.close()
