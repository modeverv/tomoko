from __future__ import annotations

import queue
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from _tools.smoke_maai_material import (
    decode_stereo_wav_16k,
    run_material_smoke,
    slice_timeline,
)


class FakeChunk:
    def __init__(self) -> None:
        self.chunks: list[np.ndarray] = []

    def put_chunk(self, chunk_data) -> None:
        self.chunks.append(np.asarray(chunk_data, dtype=np.float32))


class FakeResultQueue:
    def __init__(self) -> None:
        self._results = [
            {"p_bc_react": 0.72, "p_bc_emo": 0.04, "detail": {"frame": 1}},
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


@pytest.mark.unit
def test_decode_stereo_wav_16k_preserves_two_channels(tmp_path: Path) -> None:
    wav_path = tmp_path / "sample.wav"
    stereo = np.column_stack(
        [
            np.ones(480, dtype=np.float32) * 0.25,
            np.ones(480, dtype=np.float32) * -0.5,
        ]
    )
    pcm = np.clip(stereo * 32767, -32768, 32767).astype("<i2")
    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(pcm.tobytes())

    timeline = decode_stereo_wav_16k(wav_path)

    assert timeline.source_sample_rate == 48000
    assert timeline.source_channels == 2
    assert timeline.user_audio.size == 160
    assert timeline.tomoko_audio.size == 160
    assert float(np.mean(timeline.user_audio)) == pytest.approx(0.25, abs=0.01)
    assert float(np.mean(timeline.tomoko_audio)) == pytest.approx(-0.5, abs=0.01)


@pytest.mark.unit
def test_slice_timeline_limits_material_window(tmp_path: Path) -> None:
    timeline = decode_stereo_wav_16k(_write_stereo_wav(tmp_path, seconds=2.0))

    sliced = slice_timeline(timeline, start_sec=0.5, duration_sec=0.75)

    assert sliced.duration_sec == pytest.approx(0.75)
    assert sliced.user_audio.size == 12000
    assert sliced.tomoko_audio.size == 12000


@pytest.mark.unit
def test_decode_stereo_wav_16k_can_swap_channel_mapping(tmp_path: Path) -> None:
    timeline = decode_stereo_wav_16k(_write_stereo_wav(tmp_path, seconds=0.1))

    swapped = decode_stereo_wav_16k(
        _write_stereo_wav(tmp_path, seconds=0.1),
        swap_channels=True,
    )

    assert float(np.mean(timeline.user_audio)) == pytest.approx(0.2, abs=0.01)
    assert float(np.mean(timeline.tomoko_audio)) == pytest.approx(0.0, abs=0.01)
    assert float(np.mean(swapped.user_audio)) == pytest.approx(0.0, abs=0.01)
    assert float(np.mean(swapped.tomoko_audio)) == pytest.approx(0.2, abs=0.01)


@pytest.mark.unit
async def test_run_material_smoke_reports_session_release(tmp_path: Path) -> None:
    wav_path = _write_stereo_wav(tmp_path, seconds=0.2)

    summary = await run_material_smoke(
        input_path=wav_path,
        maai_module=FakeMaaiModule(),
        realtime_scale=0.0,
        wait_after_sec=0.05,
    )

    assert summary["source_path"] == str(wav_path)
    assert summary["raw_score_count"] == 1
    assert summary["suggestions"][0]["kind"] == "react"
    assert summary["session_releases"][0]["timeline"]["user_speaking"] is True
    assert summary["session_releases"][0]["timeline"]["tomoko_speaking"] is False
    assert summary["session_releases"][0]["emissions"][0]["type"] == "backchannel_released"
    assert summary["session_releases"][0]["audio_bytes"] > 0


def _write_stereo_wav(tmp_path: Path, *, seconds: float) -> Path:
    wav_path = tmp_path / "sample.wav"
    samples = int(round(16000 * seconds))
    user = np.ones(samples, dtype=np.float32) * 0.2
    tomoko = np.zeros(samples, dtype=np.float32)
    stereo = np.column_stack([user, tomoko])
    pcm = np.clip(stereo * 32767, -32768, 32767).astype("<i2")
    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(pcm.tobytes())
    return wav_path
