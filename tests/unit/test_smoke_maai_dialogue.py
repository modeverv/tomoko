from __future__ import annotations

import json
import queue
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from _tools.smoke_maai_dialogue import (
    DialogueTurn,
    compose_dialogue_timeline,
    run_dialogue_smoke,
)


class FakeChunk:
    def __init__(self) -> None:
        self.chunks: list[np.ndarray] = []

    def put_chunk(self, chunk_data) -> None:
        self.chunks.append(np.asarray(chunk_data, dtype=np.float32))


class FakeResultQueue:
    def __init__(self) -> None:
        self._results = [
            {
                "p_bc_react": 0.12,
                "p_bc_emo": 0.34,
                "detail": {"frame": 1},
                "x1": [0.1] * 160,
            },
            {"p_bc_react": 0.56, "p_bc_emo": 0.78, "detail": {"frame": 2}},
        ]

    def get(self, *, timeout: float):
        del timeout
        if self._results:
            return self._results.pop(0)
        raise queue.Empty


class FakeMaai:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.result_dict_queue = FakeResultQueue()
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self, wait: bool = True, timeout: float = 2.0) -> None:
        del wait, timeout
        self.started = False


class FakeMaaiModule:
    def __init__(self) -> None:
        self.chunks: list[FakeChunk] = []
        self.MaaiInput = SimpleNamespace(Chunk=self._chunk)

    def _chunk(self) -> FakeChunk:
        chunk = FakeChunk()
        self.chunks.append(chunk)
        return chunk

    def Maai(self, **kwargs) -> FakeMaai:
        return FakeMaai(**kwargs)


class TriggerResultQueue:
    def __init__(self) -> None:
        self._results = [
            {"p_bc_react": 0.72, "p_bc_emo": 0.04, "detail": {"frame": 1}},
        ]

    def get(self, *, timeout: float):
        del timeout
        if self._results:
            return self._results.pop(0)
        raise queue.Empty


class TriggerMaai(FakeMaai):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.result_dict_queue = TriggerResultQueue()


class TriggerMaaiModule(FakeMaaiModule):
    def Maai(self, **kwargs) -> TriggerMaai:
        return TriggerMaai(**kwargs)


@pytest.mark.unit
def test_compose_dialogue_timeline_places_roles_on_separate_channels() -> None:
    turns = [
        DialogueTurn(role="user", text="user", voice="Kyoko", start_sec=0.0),
        DialogueTurn(role="tomoko", text="tomoko", voice="Kyoko", start_sec=0.01),
    ]
    rendered = {
        0: np.ones(320, dtype=np.float32) * 0.25,
        1: np.ones(160, dtype=np.float32) * 0.5,
    }

    timeline = compose_dialogue_timeline(turns, rendered, sample_rate=16000)

    assert timeline.user_audio[:160] == pytest.approx([0.25] * 160)
    assert timeline.tomoko_audio[:160] == pytest.approx([0.0] * 160)
    assert timeline.tomoko_audio[160:320] == pytest.approx([0.5] * 160)


@pytest.mark.unit
async def test_run_dialogue_smoke_records_raw_maai_scores(tmp_path: Path) -> None:
    async def fake_synthesize(turn: DialogueTurn) -> np.ndarray:
        del turn
        return np.ones(320, dtype=np.float32) * 0.1

    output_path = tmp_path / "dialogue.json"

    summary = await run_dialogue_smoke(
        turns=[
            DialogueTurn(role="user", text="話してる途中なんだけど", voice="Kyoko"),
            DialogueTurn(role="tomoko", text="うん", voice="Kyoko"),
        ],
        synthesize_turn=fake_synthesize,
        maai_module=FakeMaaiModule(),
        realtime_scale=0.0,
        wait_after_sec=0.05,
        output_path=output_path,
    )

    loaded = json.loads(output_path.read_text())
    assert loaded == summary
    assert summary["maai_enabled"] is True
    assert summary["frames_sent"] >= 2
    assert summary["raw_scores"][0]["p_bc_react"] == pytest.approx(0.12)
    assert summary["raw_scores"][0]["p_bc_emo"] == pytest.approx(0.34)
    assert summary["raw_scores"][0]["raw"]["detail"]["frame"] == 1
    assert "x1" not in summary["raw_scores"][0]["raw"]
    assert summary["raw_scores"][0]["raw_omitted_keys"] == ["x1"]
    assert summary["raw_scores"][1]["p_bc_react"] == pytest.approx(0.56)
    assert summary["raw_scores"][1]["p_bc_emo"] == pytest.approx(0.78)
    assert summary["raw_score_count"] == 2
    assert summary["max_p_bc_react"] == pytest.approx(0.56)
    assert summary["max_p_bc_emo"] == pytest.approx(0.78)


@pytest.mark.unit
async def test_run_dialogue_smoke_records_session_backchannel_release() -> None:
    async def fake_synthesize(turn: DialogueTurn) -> np.ndarray:
        del turn
        return np.ones(3200, dtype=np.float32) * 0.1

    summary = await run_dialogue_smoke(
        turns=[
            DialogueTurn(role="user", text="まだ話してる途中です", voice="Kyoko"),
        ],
        synthesize_turn=fake_synthesize,
        maai_module=TriggerMaaiModule(),
        realtime_scale=0.0,
        wait_after_sec=0.05,
    )

    assert summary["suggestions"][0]["kind"] == "react"
    assert summary["session_releases"][0]["timeline"]["user_speaking"] is True
    assert summary["session_releases"][0]["timeline"]["tomoko_speaking"] is False
    assert summary["session_releases"][0]["emissions"][0]["type"] == "backchannel_released"
    assert summary["session_releases"][0]["audio_bytes"] > 0
    assert summary["session_releases"][0]["reply_done_controls"] == ["backchannel"]
