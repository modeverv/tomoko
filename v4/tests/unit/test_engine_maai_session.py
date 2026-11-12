"""MaAI をつないだ会話（P3）。MaAI は偽物にして、予測の値を試験から与える。"""

import asyncio

import numpy as np
import pytest

from engine.baseline import BaselineSession
from engine.dto import EngineConfig, MaaiFrame
from engine.sensors.vad import WINDOW, VadSensor
from engine.trace import Trace

pytestmark = pytest.mark.unit

W = WINDOW / 16_000  # 1 窓 = 0.032 秒


def loud(n):
    return np.full(WINDOW * n, 0.5, dtype=np.float32)


def quiet(n):
    return np.zeros(WINDOW * n, dtype=np.float32)


def by_level(window):
    return 1.0 if float(np.max(np.abs(window))) > 0.1 else 0.0


# ---- VAD: 言い終わりの判定を外から変える ----


def vad(**kw):
    return VadSensor(by_level, silence_s=0.384, preroll_s=0.064, min_voiced_s=0.128, **kw)


def test_vad_ends_early_when_the_policy_says_the_turn_is_over():
    v = vad()
    asked = []
    v.set_end_policy(lambda s: asked.append(round(s, 3)) or True, min_silence_s=0.224, max_silence_s=1.0)
    events = v.feed(np.concatenate([loud(10), quiet(30)]))
    ends = [e for e in events if e.kind == "end"]
    assert len(ends) == 1
    assert asked == [0.224]  # 最短の無音（7 窓）で初めて聞き、言い終わりとした
    assert ends[0].utterance.end_s == pytest.approx(10 * W)


def test_vad_holds_until_the_maximum_when_the_policy_says_wait():
    v = vad()
    v.set_end_policy(lambda s: False, min_silence_s=0.224, max_silence_s=0.64)
    events = v.feed(np.concatenate([loud(10), quiet(19)]))
    assert [e.kind for e in events] == ["start"]  # 既定の 12 窓を過ぎても、まだ言い終わりにしない
    events = v.feed(quiet(1))  # 20 窓 = 0.64 秒で、必ず言い終わりにする
    assert [e.kind for e in events] == ["end"]


def test_vad_falls_back_to_the_default_silence_when_the_policy_has_no_opinion():
    v = vad()
    v.set_end_policy(lambda s: None, min_silence_s=0.224, max_silence_s=1.0)
    events = v.feed(np.concatenate([loud(10), quiet(11)]))
    assert [e.kind for e in events] == ["start"]
    assert [e.kind for e in v.feed(quiet(1))] == ["end"]  # 既定の 12 窓


def test_vad_reports_how_long_the_current_pause_is():
    v = vad()
    v.feed(np.concatenate([loud(10), quiet(3)]))
    assert v.speaking and v.quiet_s == pytest.approx(3 * W)
    assert v.start_s == pytest.approx(0.0)


def test_vad_reports_a_second_cue_when_the_voice_goes_on_much_longer():
    v = vad(sustain_s=0.256, sustain_max_s=0.512)
    events = v.feed(np.concatenate([loud(20), quiet(12)]))
    assert [e.kind for e in events] == ["start", "sustained", "sustained_max", "end"]


# ---- 会話: 相づち、世代、停止の保留、言い終わり ----


class FakeMaai:
    """MOCK: MaAI のセンサーの偽物。latest() の値は試験が決める。"""

    def __init__(self):
        self.frame = None
        self.fed = 0
        self.tomoko = []
        self.stops = []
        self.closed = False

    def feed_mic(self, samples):
        self.fed += len(samples)
        return 0

    def latest(self):
        return self.frame

    def add_tomoko_audio(self, samples, at_mic_s):
        self.tomoko.append((len(samples), round(at_mic_s, 3)))

    def stop_tomoko(self, at_mic_s):
        self.stops.append(round(at_mic_s, 3))

    def close(self, timeout=5.0):
        self.closed = True


class Rig:
    def __init__(self, audio_s=1.0, **cfg):
        self.sent, self.messages, self.prompts = [], [], []
        self.llm_gate = asyncio.Event()
        self.llm_gate.set()
        self.audio_s = audio_s
        self.maai = FakeMaai()
        self.vad = VadSensor(by_level, silence_s=0.384, preroll_s=0.064, min_voiced_s=0.128,
                             sustain_s=0.256, sustain_max_s=0.64)
        config = {"barge_in": True, "send_chunk_s": 1.0, "maai": True, "bc_cooldown_s": 3.0,
                      "bc_min_turn_s": 1.0, "bc_arm_s": 0.4, "bc_quiet_s": 0.064}
        config.update(cfg)
        self.cfg = EngineConfig(**config)
        self.session = BaselineSession(
            self.cfg, self.vad, Trace(None), self.transcribe, self.reply, self.synthesize,
            self.send_audio, self.send_json, backchannel_clip=np.full(7200, 0.1, dtype=np.float32))
        self.session.attach_maai(self.maai)

    async def transcribe(self, audio):
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
        self.sent.append((generation, seq, len(samples)))

    async def send_json(self, obj):
        self.messages.append(obj)

    def frame(self, **values):
        """いまのマイクの時刻の予測を置き、会話に届ける。"""
        self.maai.frame = MaaiFrame(at_s=self.session._trace.audio_s, **values)
        self.session.on_maai(self.maai.frame)

    async def settle(self, until, tries=200):
        for _ in range(tries):
            if until():
                return
            await asyncio.sleep(0.005)
        raise AssertionError("待っても条件が成り立たなかった")


def clips(rig):
    return [s for s in rig.sent if s[2] == 7200]


async def test_backchannel_is_sent_in_a_short_pause_after_the_model_predicts_one():
    rig = Rig(backchannel=True)
    rig.session.on_mic(loud(40))  # 1.28 秒話している
    rig.frame(p_bc=0.8, p_now=(0.9, 0.1))  # 相づちの機会。Tomoko の番らしさは低い
    rig.session.on_mic(quiet(2))  # 64 ms の無音
    await rig.settle(lambda: clips(rig))
    await rig.session.close()
    assert clips(rig) == [(0, 0, 7200)]  # 新しい世代で、前もって作った声を送る
    assert not [m for m in rig.messages if m.get("type") == "text"]  # 会話の記録には入れない
    assert rig.maai.tomoko == [(7200, pytest.approx(1.344))]  # MaAI の Tomoko 側にも渡す


async def test_backchannel_waits_for_the_pause_and_expires():
    rig = Rig(backchannel=True)
    rig.session.on_mic(loud(40))
    rig.frame(p_bc=0.8)
    rig.session.on_mic(loud(15))  # 0.48 秒話し続け、機会（0.4 秒）が過ぎた
    rig.session.on_mic(quiet(2))
    await asyncio.sleep(0.02)
    await rig.session.close()
    assert clips(rig) == []


async def test_backchannel_respects_turn_length_cooldown_and_turn_shift():
    rig = Rig(backchannel=True)
    rig.session.on_mic(loud(20))  # 0.64 秒しか話していない
    rig.frame(p_bc=0.9)
    rig.session.on_mic(quiet(2))
    await asyncio.sleep(0.02)
    assert clips(rig) == []
    rig.session.on_mic(loud(20))
    rig.frame(p_bc=0.9, p_now=(0.2, 0.8))  # Tomoko の番らしい（言い終わり）。相づちではなく答える場面
    rig.session.on_mic(quiet(2))
    await asyncio.sleep(0.02)
    assert clips(rig) == []
    rig.session.on_mic(loud(10))
    rig.frame(p_bc=0.9, p_now=(0.8, 0.2))
    rig.session.on_mic(quiet(2))
    await rig.settle(lambda: len(clips(rig)) == 1)
    rig.session.on_mic(loud(20))
    rig.frame(p_bc=0.9, p_now=(0.8, 0.2))
    rig.session.on_mic(quiet(2))  # 前の相づちから 3 秒たっていない
    await asyncio.sleep(0.02)
    await rig.session.close()
    assert len(clips(rig)) == 1


async def test_no_backchannel_while_a_reply_is_being_made():
    rig = Rig(backchannel=True, barge_in=False)  # 割り込みで返事を捨てないようにして、作っている最中を保つ
    rig.llm_gate.clear()
    rig.session.on_mic(np.concatenate([loud(40), quiet(12)]))  # 言い終わり。返事を作り始める
    await rig.settle(lambda: len(rig.prompts) == 1)
    rig.session.on_mic(loud(40))
    rig.frame(p_bc=0.9)
    rig.session.on_mic(quiet(2))
    await asyncio.sleep(0.02)
    rig.llm_gate.set()
    await rig.session.close()
    assert clips(rig) == []


async def test_backchannel_does_not_borrow_the_generation_of_a_reply():
    rig = Rig(backchannel=True, audio_s=0.5)
    rig.session.on_mic(loud(40))
    rig.frame(p_bc=0.8)
    rig.session.on_mic(quiet(2))
    await rig.settle(lambda: clips(rig))
    rig.session.on_mic(quiet(12))  # 言い終わり。返事をする
    await rig.settle(lambda: len([m for m in rig.messages if m.get("role") == "tomoko"]) == 1)
    await rig.session.close()
    replies = [s for s in rig.sent if s[2] != 7200]
    assert {g for g, _, _ in replies} == {1}  # 相づちは世代 0、返事は世代 1。混ざらない
    assert [seq for _, seq, _ in replies] == list(range(len(replies)))


async def test_reply_audio_is_given_to_maai_and_cut_when_stopped():
    rig = Rig(audio_s=5.0)
    rig.session.on_mic(np.concatenate([loud(10), quiet(12)]))
    await rig.settle(lambda: len(rig.sent) >= 1)
    assert rig.maai.tomoko and rig.maai.tomoko[0][0] == 5 * 24_000
    rig.session.on_mic(np.concatenate([loud(10), quiet(12)]))  # 割り込み
    await rig.settle(lambda: [m for m in rig.messages if m.get("type") == "stop"])
    await rig.session.close()
    assert len(rig.maai.stops) == 1


async def test_guard_lets_a_backchannel_pass_without_stopping():
    rig = Rig(audio_s=5.0, barge_guard=True, guard_bc_det=0.45)
    rig.session.on_mic(np.concatenate([loud(10), quiet(12)]))
    await rig.settle(lambda: len([m for m in rig.messages if m.get("role") == "tomoko"]) == 1)
    rig.session.on_mic(loud(4))  # 声が鳴っている最中に、ユーザーの声が始まる
    rig.frame(p_bc_det_user=0.7)  # 相づちらしい
    rig.session.on_mic(np.concatenate([loud(5), quiet(12)]))  # 0.29 秒で 8 窓（割り込みの長さ）に届き、終わる
    await asyncio.sleep(0.05)
    await rig.session.close()
    assert not [m for m in rig.messages if m.get("type") == "stop"]
    assert rig.prompts == [["発話10"]]  # 相づちには答えない


async def test_guard_still_stops_a_real_interruption():
    rig = Rig(audio_s=5.0, barge_guard=True, guard_bc_det=0.45)
    rig.session.on_mic(np.concatenate([loud(10), quiet(12)]))
    await rig.settle(lambda: len([m for m in rig.messages if m.get("role") == "tomoko"]) == 1)
    rig.session.on_mic(loud(4))
    rig.frame(p_bc_det_user=0.2)  # 相づちらしくない
    rig.session.on_mic(loud(5))  # 8 窓で止める（従来どおり）
    await rig.settle(lambda: [m for m in rig.messages if m.get("type") == "stop"])
    await rig.session.close()


async def test_guard_gives_up_when_the_voice_goes_on_too_long():
    rig = Rig(audio_s=5.0, barge_guard=True, guard_bc_det=0.45)
    rig.session.on_mic(np.concatenate([loud(10), quiet(12)]))
    await rig.settle(lambda: len([m for m in rig.messages if m.get("role") == "tomoko"]) == 1)
    rig.session.on_mic(loud(4))
    rig.frame(p_bc_det_user=0.9)
    rig.session.on_mic(loud(10))  # 保留中
    await asyncio.sleep(0.02)
    assert not [m for m in rig.messages if m.get("type") == "stop"]
    rig.session.on_mic(loud(8))  # 20 窓（0.64 秒）に届いた。相づちにしては長いので止める
    await rig.settle(lambda: [m for m in rig.messages if m.get("type") == "stop"])
    await rig.session.close()


async def test_endpoint_policy_uses_fresh_turn_taking_predictions():
    rig = Rig(maai_endpoint=True, ep_shift_p=0.6, ep_hold_p=0.35, maai_stale_s=0.25)
    s = rig.session
    assert s._decide_end(0.3) is None  # 予測がまだない
    rig.frame(p_now=(0.2, 0.8))
    assert s._decide_end(0.3) is True  # Tomoko の番らしい
    rig.frame(p_now=(0.8, 0.2))
    assert s._decide_end(0.3) is False  # ユーザーがまだ話す
    rig.frame(p_now=(0.5, 0.5))
    assert s._decide_end(0.3) is None
    rig.frame(p_now=(0.2, 0.8))
    s.on_mic(quiet(16))  # 0.512 秒たった。予測が古い
    assert s._decide_end(0.3) is None
    await s.close()


async def test_maai_hears_the_microphone_and_is_closed_with_the_session():
    rig = Rig()
    rig.session.on_mic(loud(3))
    await rig.session.close()
    assert rig.maai.fed == 3 * WINDOW and rig.maai.closed
