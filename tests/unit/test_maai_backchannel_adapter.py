from __future__ import annotations

import io
import wave
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pytest

from server.gateway.maai_backchannel import (
    MaaiBackchannelConfig,
    MaaiBackchannelTap,
    create_maai_backchannel_tap_from_env,
)


class FakeChunk:
    FRAME_SIZE = 160

    def __init__(self) -> None:
        self.chunks: list[list[float]] = []

    def put_chunk(self, chunk_data) -> None:
        self.chunks.append([float(value) for value in chunk_data])

    def start(self) -> None:
        pass


class FakeMaai:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self, wait: bool = True, timeout: float = 2.0) -> None:
        del wait, timeout
        self.started = False


class FakeMaaiModule:
    def __init__(self) -> None:
        self.chunks: list[FakeChunk] = []
        self.maais: list[FakeMaai] = []
        self.MaaiInput = SimpleNamespace(Chunk=self._chunk)

    def _chunk(self) -> FakeChunk:
        chunk = FakeChunk()
        self.chunks.append(chunk)
        return chunk

    def Maai(self, **kwargs) -> FakeMaai:
        maai = FakeMaai(**kwargs)
        self.maais.append(maai)
        return maai


def _wav_bytes(samples: np.ndarray, *, sample_rate: int = 16000) -> bytes:
    pcm = np.clip(samples, -1.0, 1.0)
    pcm_i16 = (pcm * 32767).astype("<i2")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm_i16.tobytes())
    return output.getvalue()


@pytest.mark.unit
async def test_maai_backchannel_tap_starts_bc_2type_chunk_model() -> None:
    fake_module = FakeMaaiModule()
    tap = MaaiBackchannelTap(
        config=MaaiBackchannelConfig(),
        maai_module=fake_module,
    )

    await tap.start()

    assert len(fake_module.chunks) == 2
    assert fake_module.maais[0].started is True
    assert fake_module.maais[0].kwargs["mode"] == "bc_2type"
    assert fake_module.maais[0].kwargs["lang"] == "jp"
    assert fake_module.maais[0].kwargs["audio_ch1"] is fake_module.chunks[0]
    assert fake_module.maais[0].kwargs["audio_ch2"] is fake_module.chunks[1]


@pytest.mark.unit
async def test_maai_backchannel_tap_feeds_user_audio_with_silent_system_channel() -> None:
    fake_module = FakeMaaiModule()
    tap = MaaiBackchannelTap(
        config=MaaiBackchannelConfig(),
        maai_module=fake_module,
    )
    await tap.start()
    user_audio = np.ones(320, dtype=np.float32) * 0.25

    tap.observe_user_audio(user_audio, observed_at=datetime.now(UTC))

    assert len(fake_module.chunks[0].chunks) == 2
    assert len(fake_module.chunks[1].chunks) == 2
    assert fake_module.chunks[0].chunks[0] == pytest.approx([0.25] * 160)
    assert fake_module.chunks[1].chunks[0] == pytest.approx([0.0] * 160)


@pytest.mark.unit
async def test_maai_backchannel_tap_decodes_tomoko_wav_to_system_channel() -> None:
    fake_module = FakeMaaiModule()
    tap = MaaiBackchannelTap(
        config=MaaiBackchannelConfig(),
        maai_module=fake_module,
    )
    await tap.start()
    wav_bytes = _wav_bytes(np.ones(320, dtype=np.float32) * 0.5)

    tap.observe_tomoko_audio(wav_bytes, observed_at=datetime.now(UTC))

    assert len(fake_module.chunks[0].chunks) == 2
    assert len(fake_module.chunks[1].chunks) == 2
    assert fake_module.chunks[0].chunks[0] == pytest.approx([0.0] * 160)
    assert fake_module.chunks[1].chunks[0] == pytest.approx([0.5] * 160, abs=0.001)


@pytest.mark.unit
async def test_maai_backchannel_tap_feeds_duplex_audio_frame() -> None:
    fake_module = FakeMaaiModule()
    tap = MaaiBackchannelTap(
        config=MaaiBackchannelConfig(),
        maai_module=fake_module,
    )
    await tap.start()

    tap.observe_duplex_audio(
        user_chunk=np.ones(160, dtype=np.float32) * 0.2,
        tomoko_chunk=np.ones(160, dtype=np.float32) * 0.4,
        observed_at=datetime.now(UTC),
    )

    assert len(fake_module.chunks[0].chunks) == 1
    assert len(fake_module.chunks[1].chunks) == 1
    assert fake_module.chunks[0].chunks[0] == pytest.approx([0.2] * 160)
    assert fake_module.chunks[1].chunks[0] == pytest.approx([0.4] * 160)


@pytest.mark.unit
async def test_maai_backchannel_tap_emits_thresholded_suggestion_with_cooldown() -> None:
    suggestions = []
    tap = MaaiBackchannelTap(
        config=MaaiBackchannelConfig(
            react_threshold=0.7,
            emo_threshold=0.8,
            cooldown_ms=1000,
        ),
        suggestion_callback=suggestions.append,
        maai_module=FakeMaaiModule(),
    )
    now = datetime.now(UTC)

    tap.handle_result({"p_bc_react": 0.71, "p_bc_emo": 0.1}, observed_at=now)
    tap.handle_result({"p_bc_react": 0.99, "p_bc_emo": 0.1}, observed_at=now)
    tap.handle_result({"p_bc_react": 0.1, "p_bc_emo": 0.9}, observed_at=now)

    assert len(suggestions) == 1
    assert suggestions[0].kind == "react"
    assert suggestions[0].score == pytest.approx(0.71)
    assert suggestions[0].source == "maai"


@pytest.mark.unit
def test_maai_backchannel_config_uses_production_react_threshold() -> None:
    assert MaaiBackchannelConfig().react_threshold == pytest.approx(0.50)


@pytest.mark.unit
def test_create_maai_backchannel_tap_from_env_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("TOMOKO_MAAI_BACKCHANNEL_ENABLED", raising=False)

    assert create_maai_backchannel_tap_from_env() is None


@pytest.mark.unit
def test_create_maai_backchannel_tap_from_env_reads_thresholds(monkeypatch) -> None:
    monkeypatch.setenv("TOMOKO_MAAI_BACKCHANNEL_ENABLED", "1")
    monkeypatch.setenv("TOMOKO_MAAI_REACT_THRESHOLD", "0.6")
    monkeypatch.setenv("TOMOKO_MAAI_EMO_THRESHOLD", "0.75")

    tap = create_maai_backchannel_tap_from_env(maai_module=FakeMaaiModule())

    assert tap is not None
    assert tap.config.react_threshold == 0.6
    assert tap.config.emo_threshold == 0.75


@pytest.mark.unit
def test_create_maai_backchannel_tap_from_env_uses_production_react_default(
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOMOKO_MAAI_BACKCHANNEL_ENABLED", "1")
    monkeypatch.delenv("TOMOKO_MAAI_REACT_THRESHOLD", raising=False)

    tap = create_maai_backchannel_tap_from_env(maai_module=FakeMaaiModule())

    assert tap is not None
    assert tap.config.react_threshold == pytest.approx(0.50)
