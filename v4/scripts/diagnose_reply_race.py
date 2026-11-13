"""MOCK: 同じ入力で TTS の解除時刻だけを変え、現在の返答の競合を記録する。

    python -m scripts.diagnose_reply_race --out /tmp/reply-race.json

実際の BaselineSession / VadSensor を使う。ワーカー、VAD確率、時計、波形は合成。
MaAI・モデル・ネットワークは使わない。音声の受信・実再生を測る道具ではなく、
送信呼び出しまでの制御順序の診断であり、新しい方針の正しさを主張しない。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import baseline
from engine.dto import IN_SR, OUT_SR, EngineConfig
from engine.sensors.vad import WINDOW, VadSensor
from engine.trace import Trace

RELEASE_WINDOWS = {"before_resume": 18, "during_grace": 31, "after_sustained": 39}
FIRST_VOICED_WINDOWS = RESUMED_VOICED_WINDOWS = 32
PAUSE_WINDOWS = 22  # small-v2/T5 の明示pause 0.7秒を、この合成診断では32ms窓へ丸める。
TRAILING_QUIET_WINDOWS = 13
WINDOW_S = WINDOW / IN_SR


class _ClockLoop:
    # MOCK: セッションが読む時刻だけを入力の時計にする。asyncio の実時計は変えない。
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop, self.now_s = loop, 0.0

    def time(self) -> float:
        return self.now_s

    def __getattr__(self, name: str) -> Any:
        return getattr(self._loop, name)


class _NumericTrace(Trace):
    # MOCK: 実音声や文字を含めず、制御イベントと数だけを収集する。
    def __init__(self) -> None:
        super().__init__(None)
        self.events: list[dict] = []

    def event(self, name: str, **fields: Any) -> None:
        numeric = {key: value for key, value in fields.items()
                   if isinstance(value, (int, float, bool)) or value is None}
        self.events.append({"event": name, "mic_s": round(self.audio_s, 6)} | numeric)


async def _drain() -> None:
    # MOCK: 実時間を待たず、既存ワーカーのタスクとキャンセルを進める。
    for _ in range(12):
        await asyncio.sleep(0)


async def _run_case(name: str, cfg: EngineConfig) -> dict:
    clock, trace = _ClockLoop(asyncio.get_running_loop()), _NumericTrace()
    gate = asyncio.Event()
    stt_inputs: list[int] = []
    tts_calls = 0

    # MOCK: 入力の音量だけで有声判定する。Silero や他のモデルは読み込まない。
    vad = VadSensor(lambda window: float(np.max(np.abs(window)) > 0.1),
                    cfg.vad_threshold, cfg.vad_silence_s, cfg.vad_preroll_s,
                    cfg.vad_min_voiced_s, sustain_s=cfg.barge_in_voiced_s)

    async def transcribe(audio: np.ndarray) -> str:
        # MOCK: 固定の書き起こし。文字は診断結果へ保存しない。
        stt_inputs.append(len(audio))
        return "合成入力"

    async def reply(messages: list[dict]) -> AsyncIterator[str]:
        # MOCK: 1句だけを直ちに返す。LLM は使わない。
        yield "合成返答。"

    async def synthesize(phrase: str) -> np.ndarray:
        # MOCK: 最初の TTS だけをゲートで止める。2回目は直ちに返す。
        nonlocal tts_calls
        tts_calls += 1
        if tts_calls == 1:
            trace.event("mock_tts_wait", call=tts_calls)
            try:
                await gate.wait()
            except asyncio.CancelledError:
                trace.event("mock_tts_cancelled", call=1)
                raise
        return np.full(OUT_SR, 0.1, dtype=np.float32)

    async def send_audio(generation: int, seq: int, audio: np.ndarray) -> None:
        # MOCK: 通信の代わりに、送信を呼んだ入力時刻と VAD の状態を記録する。
        trace.event("send_audio", generation=generation, seq=seq,
                    samples=len(audio), user_speaking=vad.speaking)

    async def send_json(message: dict) -> None:
        # MOCK: 表示文字は捨てる。stop 自体は BaselineSession の Trace に残る。
        pass

    facade = SimpleNamespace(**vars(asyncio))
    facade.get_running_loop = lambda: clock
    release_at = FIRST_VOICED_WINDOWS + RELEASE_WINDOWS[name]
    total = (FIRST_VOICED_WINDOWS + PAUSE_WINDOWS + RESUMED_VOICED_WINDOWS
             + TRAILING_QUIET_WINDOWS)
    resume_at = FIRST_VOICED_WINDOWS + PAUSE_WINDOWS
    with patch.object(baseline, "asyncio", facade):
        session = baseline.BaselineSession(
            cfg, vad, trace, transcribe, reply, synthesize, send_audio, send_json)
        try:
            for index in range(total):
                voiced = (index < FIRST_VOICED_WINDOWS
                          or resume_at <= index < resume_at + RESUMED_VOICED_WINDOWS)
                clock.now_s = (index + 1) * WINDOW_S
                session.on_mic(np.full(WINDOW, 0.5 if voiced else 0.0, dtype=np.float32))
                if index + 1 == release_at:
                    trace.event("mock_tts_release", call=1)
                    gate.set()
                await _drain()
            await _drain()
        finally:
            await session.close()
            await _drain()

    if any(event["event"] == "turn_failed" for event in trace.events):
        raise RuntimeError("synthetic turn failed; diagnostic sequence is incomplete")
    first_sends = [e for e in trace.events if e["event"] == "send_audio" and e["generation"] == 0]
    ready = [e for e in trace.events if e["event"] == "tts_first_audio" and e["turn"] == 1]
    ends = [e for e in trace.events if e["event"] == "vad_end"]
    resume_s = resume_at * WINDOW_S
    sustained = [e for e in trace.events
                 if e["event"] == "vad_sustained" and e["at_s"] > resume_s]
    return {
        "case": name,
        "tts_release_s": round(release_at * WINDOW_S, 6),
        "first_vad_end_s": None if not ends else ends[0]["mic_s"],
        "resume_s": round(resume_s, 6),
        "resumed_sustained_s": None if not sustained else sustained[0]["mic_s"],
        "first_tts_audio_s": None if not ready else ready[0]["mic_s"],
        "first_send_s": None if not first_sends else first_sends[0]["mic_s"],
        "first_send_user_speaking": None if not first_sends else first_sends[0]["user_speaking"],
        "discard_reply": any(e["event"] == "discard_reply" for e in trace.events),
        "stop": any(e["event"] == "stop" for e in trace.events),
        "stt_input_samples": stt_inputs,
        "events": trace.events,
    }


async def run_diagnostic(cases: Sequence[str] = tuple(RELEASE_WINDOWS)) -> dict:
    """MOCK: 同じ合成入力を、指定した TTS 解除順で逐次に通す。"""
    if any(name not in RELEASE_WINDOWS for name in cases):
        raise ValueError("unknown release case")
    cfg = EngineConfig(barge_in=True, send_chunk_s=1.0, maai=False,
                       maai_endpoint=False, backchannel=False, barge_guard=False)
    rows = [await _run_case(name, cfg) for name in cases]
    return {
        "schema_version": 1,
        "mock": True,
        "config": {name: getattr(cfg, name) for name in (
            "vad_silence_s", "barge_in_voiced_s", "barge_in", "maai",
            "maai_endpoint", "backchannel", "barge_guard")},
        "input": {
            "sample_rate": IN_SR, "window_samples": WINDOW,
            "first_voiced_s": FIRST_VOICED_WINDOWS * WINDOW_S,
            "pause_requested_s": 0.7, "pause_s": PAUSE_WINDOWS * WINDOW_S,
            "resumed_voiced_s": RESUMED_VOICED_WINDOWS * WINDOW_S,
        },
        "cases": rows,
    }


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("all", *RELEASE_WINDOWS), default="all")
    parser.add_argument("--out", type=Path, help="数値の JSON。省略すると標準出力へ書く")
    args = parser.parse_args(argv)
    cases = tuple(RELEASE_WINDOWS) if args.case == "all" else (args.case,)
    payload = json.dumps(asyncio.run(run_diagnostic(cases)), indent=2, allow_nan=False) + "\n"
    if args.out is None:
        print(payload, end="")
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload)


if __name__ == "__main__":
    main()
