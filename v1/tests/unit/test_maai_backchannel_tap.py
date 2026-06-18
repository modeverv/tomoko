from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import pytest

from server.edge.pipeline.vad import VADProcessor
from server.gateway.turn_taking.barge_in import BargeInDetector
from server.session import TomoroSession
from server.shared.models import AudioChunkOut


class QuietVAD:
    def process_chunk(self, chunk: np.ndarray) -> float:
        del chunk
        return 0.0


class RecordingAudioTap:
    def __init__(self) -> None:
        self.user_chunks: list[np.ndarray] = []
        self.tomoko_chunks: list[bytes] = []

    def observe_user_audio(self, chunk: np.ndarray, *, observed_at: datetime) -> None:
        del observed_at
        self.user_chunks.append(chunk.copy())

    def observe_tomoko_audio(self, chunk: bytes, *, observed_at: datetime) -> None:
        del observed_at
        self.tomoko_chunks.append(chunk)


class FailingAudioTap:
    def observe_user_audio(self, chunk: np.ndarray, *, observed_at: datetime) -> None:
        del chunk, observed_at
        raise RuntimeError("tap user failure")

    def observe_tomoko_audio(self, chunk: bytes, *, observed_at: datetime) -> None:
        del chunk, observed_at
        raise RuntimeError("tap tomoko failure")


def _session(
    *,
    send_audio: Any | None = None,
    audio_interaction_tap: Any | None = None,
    send_event: Any | None = None,
) -> TomoroSession:
    return TomoroSession(
        vad_processor=VADProcessor(vad=QuietVAD(), silence_ms=400),
        send_event=send_event or (lambda event: None),
        send_audio=send_audio,
        barge_in_detector=BargeInDetector(),
        audio_interaction_tap=audio_interaction_tap,
    )


@pytest.mark.unit
async def test_user_audio_is_copied_to_optional_interaction_tap() -> None:
    tap = RecordingAudioTap()
    session = _session(audio_interaction_tap=tap)
    chunk = np.linspace(-0.25, 0.25, 512, dtype=np.float32)

    segment = await session.process_audio_chunk(chunk.tobytes())

    assert segment is None
    assert len(tap.user_chunks) == 1
    np.testing.assert_array_equal(tap.user_chunks[0], chunk)


@pytest.mark.unit
async def test_audio_tap_failure_does_not_block_user_hot_path() -> None:
    session = _session(audio_interaction_tap=FailingAudioTap())
    chunk = np.ones(512, dtype=np.float32)

    segment = await session.process_audio_chunk(chunk.tobytes())

    assert segment is None
    assert session.get_now_state().vad_state == "idle"


@pytest.mark.unit
async def test_tomoko_audio_is_sent_and_copied_to_optional_interaction_tap() -> None:
    sent_audio: list[bytes] = []
    tap = RecordingAudioTap()
    session = _session(send_audio=sent_audio.append, audio_interaction_tap=tap)
    chunk = AudioChunkOut(data=b"RIFFfakeWAVE", sequence=0, is_last=True)

    await session._send_audio_chunk(chunk)

    assert sent_audio == [b"RIFFfakeWAVE"]
    assert tap.tomoko_chunks == [b"RIFFfakeWAVE"]


@pytest.mark.unit
async def test_tomoko_audio_tap_failure_does_not_block_audio_send() -> None:
    sent_audio: list[bytes] = []
    session = _session(send_audio=sent_audio.append, audio_interaction_tap=FailingAudioTap())
    chunk = AudioChunkOut(data=b"pcm", sequence=0, is_last=True)

    await session._send_audio_chunk(chunk)

    assert sent_audio == [b"pcm"]


class MockVAPAudioTap:
    def __init__(self, recommended_ms: int | None = None) -> None:
        self.recommended_ms = recommended_ms

    def observe_user_audio(self, chunk: np.ndarray, *, observed_at: datetime) -> None:
        pass

    def observe_tomoko_audio(self, chunk: bytes, *, observed_at: datetime) -> None:
        pass

    def get_recommended_silence_ms(self) -> int | None:
        return self.recommended_ms


@pytest.mark.unit
async def test_session_applies_recommended_silence_from_audio_tap() -> None:
    tap = MockVAPAudioTap(recommended_ms=250)
    session = _session(audio_interaction_tap=tap)

    assert session.vad_processor.silence_ms == 400

    chunk = np.zeros(512, dtype=np.float32)
    await session.process_audio_chunk(chunk.tobytes())

    assert session.vad_processor.silence_ms == 250


@pytest.mark.unit
def test_create_maai_backchannel_tap_from_config() -> None:
    from server.gateway.maai_backchannel import create_maai_backchannel_tap_from_env
    from server.shared.config import AudioSection

    # 1. Delta config
    audio_cfg = AudioSection(
        sample_rate=16000,
        chunk_ms=32,
        vad_silence_ms=400,
        vap_hybrid_enabled=True,
        vap_hybrid_min_silence_ms=120,
        vap_hybrid_delta_silence_ms=580,
        vap_hybrid_threshold_probability=0.85,
    )
    # mock maai module so it doesn't fail on import
    mock_maai = object()
    tap = create_maai_backchannel_tap_from_env(
        maai_module=mock_maai,
        config_audio=audio_cfg,
    )
    assert tap is not None
    assert tap.config.vap_hybrid_enabled is True
    assert tap.config.min_silence_ms == 120
    assert tap.config.delta_silence_ms == 580
    assert tap.config.threshold_probability == 0.85

