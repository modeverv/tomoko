"""MOCK: 相づちと本返答の順次再生を、偽時計・合成波形・Player で確認する。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from test_engine_maai_session import Rig, clips, loud, quiet

from bench.player import Player
from engine import baseline

pytestmark = pytest.mark.unit


@pytest.fixture
async def playback(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[Rig, Player, Mock]]:
    # asyncio 自体の時計は変えず、このセッションの再生時計だけを動かす。
    loop = Mock(wraps=asyncio.get_running_loop())
    loop.time.return_value = 0.0
    facade = SimpleNamespace(**vars(asyncio))
    facade.get_running_loop = lambda: loop
    monkeypatch.setattr(baseline, "asyncio", facade)
    rig = Rig(backchannel=True, audio_s=0.1)
    player = Player(24_000)

    async def reply(messages: list[dict]) -> AsyncIterator[str]:
        rig.prompts.append([m["content"] for m in messages])
        yield "返答。"

    async def send_audio(generation: int, seq: int, audio: np.ndarray) -> None:
        rig.sent.append((generation, seq, len(audio)))
        player.on_chunk(loop.time(), generation, audio)

    async def send_json(message: dict) -> None:
        rig.messages.append(message)
        if message.get("type") == "stop":
            player.on_stop(loop.time(), message["generation"])

    rig.session._reply = reply
    rig.session._send_audio, rig.session._send_json = send_audio, send_json
    try:
        yield rig, player, loop.time
    finally:
        await rig.session.close()


async def send_backchannel(rig: Rig) -> None:
    rig.session.on_mic(loud(40))
    rig.frame(p_bc=0.8)
    rig.session.on_mic(quiet(2))
    await rig.settle(lambda: clips(rig))


async def finish_turn(rig: Rig, replies: int = 1) -> None:
    rig.session.on_mic(quiet(12))
    await rig.settle(
        lambda: len([m for m in rig.messages if m.get("role") == "tomoko"]) == replies)


def configure(rig: Rig, **values: object) -> None:
    rig.cfg = replace(rig.cfg, **values)
    rig.session._cfg = rig.cfg


async def test_fast_reply_includes_the_backchannel_queue_in_its_playback_time(playback) -> None:
    rig, player, now = playback
    await send_backchannel(rig)
    assert not rig.session._playing()  # 相づちだけでは発言権を取らない。
    await finish_turn(rig)
    assert rig.session._play_end == pytest.approx(len(player.render()) / player.sr)
    now.return_value = 0.35
    assert player.played(now())[0] == 1  # 相づちの後ろの本返答が鳴っている。
    assert player.level(now(), window=0.01) > 0
    assert rig.session._playing()
    now.return_value = 0.41
    assert not rig.session._playing()


@pytest.mark.parametrize("guard,voice_windows", [(False, 4), (True, 9)])
async def test_user_backchannel_during_the_queued_reply_is_ignored(
    playback, guard: bool, voice_windows: int,
) -> None:
    rig, player, now = playback
    configure(rig, barge_guard=guard)
    await send_backchannel(rig)
    await finish_turn(rig)
    now.return_value = 0.35
    assert player.played(now())[0] == 1
    rig.session.on_mic(loud(4))
    rig.frame(p_bc_det_user=0.7)
    if voice_windows > 4:
        rig.session.on_mic(loud(voice_windows - 4))
    rig.session.on_mic(quiet(12))
    assert rig.session._queue.empty()
    assert len(rig.prompts) == 1
    assert not [m for m in rig.messages if m.get("type") == "stop"]


async def test_interruption_stops_queued_reply_and_resets_the_output_queue(playback) -> None:
    rig, player, now = playback
    configure(rig, barge_guard=True)
    await send_backchannel(rig)
    await finish_turn(rig)
    now.return_value = 0.35
    rig.session.on_mic(loud(4))
    rig.frame(p_bc_det_user=0.2)
    rig.session.on_mic(loud(5))
    await rig.settle(lambda: [m for m in rig.messages if m.get("type") == "stop"])
    assert [m for m in rig.messages if m.get("type") == "stop"] == [
        {"type": "stop", "generation": 1}]
    assert not rig.session._playing()
    await finish_turn(rig, replies=2)
    assert rig.session._play_end == pytest.approx(0.45)
    assert len(player.render()) / player.sr == pytest.approx(0.45)


async def test_backchannels_do_not_queue_over_one_another(playback) -> None:
    rig, _, _ = playback
    configure(rig, bc_cooldown_s=0.0)
    await send_backchannel(rig)
    rig.session.on_mic(loud(4))
    rig.frame(p_bc=0.9)
    rig.session.on_mic(quiet(2))
    for _ in range(5):
        await asyncio.sleep(0)
    assert len(clips(rig)) == 1


async def test_pending_backchannel_send_remains_owned_when_its_clip_time_expires(playback) -> None:
    rig, _, now = playback
    configure(rig, bc_cooldown_s=0.0)
    started, release = asyncio.Event(), asyncio.Event()
    send = rig.session._send_audio

    async def blocked_send(generation: int, seq: int, audio: np.ndarray) -> None:
        started.set()
        await release.wait()
        await send(generation, seq, audio)

    rig.session._send_audio = blocked_send
    rig.session.on_mic(loud(40))
    rig.frame(p_bc=0.8)
    rig.session.on_mic(quiet(2))
    await started.wait()
    first = rig.session._bc_task
    try:
        now.return_value = 0.4  # 0.3秒の音声は終わる時刻だが、送信はまだ待機中。
        rig.session.on_mic(loud(4))
        rig.frame(p_bc=0.9)
        rig.session.on_mic(quiet(2))
        assert rig.session._bc_task is first
        await rig.session.close()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not rig.sent
    finally:
        release.set()
        await asyncio.gather(first, rig.session._bc_task, return_exceptions=True)


async def test_maai_audio_failure_keeps_the_same_backchannel_and_reply_queue(playback) -> None:
    rig, player, now = playback

    def fail_audio(samples: np.ndarray, at_mic_s: float) -> None:
        raise RuntimeError("MOCK: MaAI audio failed")

    rig.maai.add_tomoko_audio = fail_audio
    await send_backchannel(rig)
    assert rig.session._maai is None
    await finish_turn(rig)
    now.return_value = 0.35
    assert player.played(now())[0] == 1
    assert rig.session._playing()
    assert rig.session._play_end == pytest.approx(len(player.render()) / player.sr)
