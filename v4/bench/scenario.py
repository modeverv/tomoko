"""シナリオ（台本）と、生成した入力の注釈の型。"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

AXES = ("T1", "T2", "T3", "T4", "T5", "C2", "C3")
_PAUSE = re.compile(r"\[pause\s+([0-9.]+)\]")


@dataclass(frozen=True)
class Step:
    """ユーザー側の 1 発話。引き金は 3 つのうち 1 つだけを指定する。

    at                   決まった時刻に流す
    after_tomoko_speaks  Tomoko が話し続けて N 秒たったら流す（割り込み、相づち）
    after_reply          前の発話への返事が終わって N 秒後に流す。返事がなければ、
                         応答を待つ窓が切れた時点で流す（複数ターン）
    """

    say: str
    at: float | None = None
    after_tomoko_speaks: float | None = None
    after_reply: float | None = None

    def __post_init__(self) -> None:
        given = [v for v in (self.at, self.after_tomoko_speaks, self.after_reply) if v is not None]
        if len(given) != 1:
            raise ValueError(f"引き金は 1 つだけ指定する: {self.say!r}")

    def parts(self) -> list[str | float]:
        """台本を、読む文字列と無音の秒数に分ける。「あの[pause 1.2]それで」"""
        out: list[str | float] = []
        pos = 0
        for m in _PAUSE.finditer(self.say):
            if m.start() > pos:
                out.append(self.say[pos : m.start()])
            out.append(float(m.group(1)))
            pos = m.end()
        if pos < len(self.say):
            out.append(self.say[pos:])
        return out

    def text(self) -> str:
        return _PAUSE.sub("", self.say)


@dataclass(frozen=True)
class Scenario:
    id: str
    axis: str
    steps: tuple[Step, ...]
    expect: dict = field(default_factory=dict)
    voice: str | None = None

    def __post_init__(self) -> None:
        if self.axis not in AXES:
            raise ValueError(f"知らない軸: {self.axis}")
        if not self.steps or self.steps[0].at is None:
            raise ValueError(f"{self.id}: 最初の発話は at で始める")
        need = {"T3": 2, "T4": 2}.get(self.axis, 1)
        if len(self.steps) < need:
            raise ValueError(f"{self.id}: {self.axis} は発話が {need} つ要る")
        if self.axis in ("T3", "T4") and self.steps[-1].after_tomoko_speaks is None:
            raise ValueError(f"{self.id}: {self.axis} の最後の発話は after_tomoko_speaks で流す")


def load_scenarios(path: Path) -> list[Scenario]:
    """1 ファイル、またはディレクトリの *.json を読む。"""
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    out: list[Scenario] = []
    for f in files:
        for raw in json.loads(f.read_text()):
            steps = tuple(Step(**s) for s in raw["steps"])
            out.append(
                Scenario(raw["id"], raw["axis"], steps, raw.get("expect", {}), raw.get("voice"))
            )
    ids = [s.id for s in out]
    if len(ids) != len(set(ids)):
        raise ValueError("シナリオの id が重なっている")
    return out


@dataclass(frozen=True)
class StepAudio:
    """生成した 1 発話。voiced と pauses は、発話の先頭からの秒。"""

    wav: str
    duration: float
    voiced: list[list[float]]
    pauses: list[list[float]]


@dataclass(frozen=True)
class Manifest:
    """1 シナリオぶんの入力。scenario.json として保存する。"""

    scenario: Scenario
    audio: tuple[StepAudio, ...]
    private: bool = False  # 本人の録音。採点役（クラウド）に送らない
    one_pass: bool = False  # True は無音コマンドを埋めた 1 回合成。False は従来の部品ごとの合成

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "scenario.json").write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=1)
        )

    @staticmethod
    def load(directory: Path) -> Manifest:
        raw = json.loads((directory / "scenario.json").read_text())
        s = raw["scenario"]
        scenario = Scenario(
            s["id"], s["axis"], tuple(Step(**x) for x in s["steps"]), s["expect"], s["voice"]
        )
        return Manifest(
            scenario, tuple(StepAudio(**a) for a in raw["audio"]), raw.get("private", False),
            raw.get("one_pass", False),
        )
