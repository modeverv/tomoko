"""時刻の採点。入力の注釈（ユーザーの発話の時刻）と、Tomoko の発話区間だけを使う。

定義は _docs/bench-definitions.md。Full-Duplex-Bench の項目を参考に、定義から書いた。
"""

import itertools
from dataclasses import dataclass

from bench import definitions as d

Seg = tuple[float, float]


@dataclass(frozen=True)
class StepTime:
    """ユーザーの 1 発話を、実際に流した時刻（実行の先頭からの秒）で表す。"""

    start: float
    end: float
    voiced: tuple[Seg, ...]
    pauses: tuple[Seg, ...]


def _overlap(seg: Seg, a: float, b: float) -> float:
    return max(0.0, min(seg[1], b) - max(seg[0], a))


def _mains(tomoko: list[Seg]) -> list[Seg]:
    return [s for s in tomoko if s[1] - s[0] >= d.MAIN_MIN_S]


def _shorts(tomoko: list[Seg]) -> list[Seg]:
    return [s for s in tomoko if s[1] - s[0] < d.MAIN_MIN_S]


def _reply_onset(mains: list[Seg], end: float, not_before: float | None = None) -> float | None:
    """言い終わりのあと、窓の中で始まった最初の本発話。"""
    lo = end - d.ONSET_TOLERANCE_S if not_before is None else max(end - d.ONSET_TOLERANCE_S, not_before)
    for s in mains:
        if lo <= s[0] <= end + d.RESPONSE_WINDOW_S:
            return s[0]
    return None


def _turn(target: StepTime, tomoko: list[Seg]) -> dict:
    mains = _mains(tomoko)
    # 早すぎ: ユーザーが話している間に本発話を始めた
    early = any(target.start <= s[0] < target.end - d.ONSET_TOLERANCE_S for s in mains)
    # かぶせ続けた: ユーザーが話し始めたときに話していて、窓の中で譲らなかった
    talked_over = any(
        s[0] < target.start and s[1] > min(target.end, target.start + d.STOP_WINDOW_S) for s in mains
    )
    responded = any(
        _overlap(s, target.end, target.end + d.RESPONSE_WINDOW_S) > 0
        and s[0] <= target.end + d.RESPONSE_WINDOW_S
        for s in mains
    )
    onset = _reply_onset(mains, target.end)
    latency = None if early or talked_over or onset is None else max(0.0, onset - target.end)
    after = [s for s in mains if s[0] >= target.end - d.ONSET_TOLERANCE_S]
    double = any(b[0] - a[1] >= d.DOUBLE_ANSWER_GAP_S for a, b in itertools.pairwise(after))
    return {"responded": responded, "early": early, "talked_over": talked_over,
            "latency": latency, "double_answer": double}


def _active_main(tomoko: list[Seg], t: float) -> Seg | None:
    for s in _mains(tomoko):
        if s[0] <= t < s[1]:
            return s
    return None


def _t2(target: StepTime, tomoko: list[Seg]) -> dict:
    mains = _mains(tomoko)
    # 言いよどみの間に本発話を始めた、または、その間に本発話をしていた
    interrupted = any(
        any(a <= s[0] < b for s in mains)
        or sum(_overlap(s, a, b) for s in mains) >= d.OVERLAP_MIN_S
        for a, b in target.pauses
    )
    responded = any(_overlap(s, target.end, target.end + d.RESPONSE_WINDOW_S) > 0 for s in mains)
    return {"interrupted": interrupted, "responded": responded}


def _t3(bc: StepTime, tomoko: list[Seg], failed: bool) -> dict:
    seg = None if failed else _active_main(tomoko, bc.start)
    if seg is None:
        return {"precondition": False, "interrupted": None}
    later = [s for s in tomoko if s[0] >= seg[1]]
    stays_silent = not later or later[0][0] - seg[1] >= d.STAY_SILENT_S
    went_silent = seg[1] <= bc.start + d.STOP_WINDOW_S
    return {"precondition": True, "interrupted": went_silent and stays_silent}


def _t4(cut: StepTime, tomoko: list[Seg], failed: bool) -> dict:
    seg = None if failed else _active_main(tomoko, cut.start)
    if seg is None:
        return {
            "precondition": False, "stopped": None, "stop_latency": None,
            "responded": None, "latency": None,
        }
    stop_latency = seg[1] - cut.start
    onset = _reply_onset(_mains(tomoko), cut.end, not_before=seg[1])
    return {
        "precondition": True,
        "stopped": stop_latency <= d.STOP_WINDOW_S,
        "stop_latency": stop_latency,
        "responded": onset is not None,
        "latency": None if onset is None else max(0.0, onset - cut.end),
    }


def _t5(talk: StepTime, tomoko: list[Seg]) -> dict:
    inside = [s for s in tomoko if talk.start <= s[0] < talk.end]
    shorts = _shorts(inside)
    overlapped = 0
    for s in shorts:
        on_voice = sum(_overlap(s, a, b) for a, b in talk.voiced)
        if on_voice > (s[1] - s[0]) / 2:
            overlapped += 1
    mains = _mains(tomoko)
    minutes = (talk.end - talk.start) / 60
    return {
        "count": len(shorts),
        "per_minute": len(shorts) / minutes if minutes > 0 else 0.0,
        "in_gap": len(shorts) - overlapped,
        "overlapped": overlapped,
        "bursts": sum(1 for a, b in itertools.pairwise(shorts) if b[0] - a[0] < d.BURST_GAP_S),
        "main_interruptions": sum(
            1 for s in mains if _overlap(s, talk.start, talk.end) >= d.OVERLAP_MIN_S
        ),
    }


def score_timing(
    axis: str, steps: list[StepTime], tomoko: list[Seg], precondition_failed: bool = False
) -> dict:
    """1 回の実行を採点する。答えなかった回も、必ず結果を返す（分母から外さない）。"""
    tomoko = sorted(tomoko)
    out: dict = {"axis": axis, "silent": not tomoko}
    if axis == "T2":
        out |= _t2(steps[0], tomoko)
    elif axis == "T3":
        out |= _t3(steps[-1], tomoko, precondition_failed)
    elif axis == "T4":
        out |= _t4(steps[-1], tomoko, precondition_failed)
    elif axis == "T5":
        out |= _t5(steps[0], tomoko)
    else:  # T1、C2、C3 は、最後の発話への応答を見る
        out |= _turn(steps[-1], tomoko)
    return out
