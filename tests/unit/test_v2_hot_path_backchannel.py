from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Queue

import pytest

from server.hot_path.backchannel import (
    BackchannelAssetStore,
    MaaiBackchannelConfig,
    MaaiBackchannelDetector,
    _read_maai_result_once,
    create_backchannel_detector_from_env,
)

pytestmark = pytest.mark.unit


def _wav_bytes(label: str) -> bytes:
    return b"RIFF" + label.encode("ascii").ljust(4, b"_") + b"WAVEdata"


def _write_assets(path: Path) -> None:
    path.mkdir(exist_ok=True)
    for name in ("un", "hee", "hou"):
        (path / f"{name}.wav").write_bytes(_wav_bytes(name))


def test_backchannel_assets_are_limited_to_three_fixed_utterances(tmp_path: Path) -> None:
    _write_assets(tmp_path)
    store = BackchannelAssetStore(tmp_path)

    first = store.next_chunk()
    second = store.next_chunk()
    third = store.next_chunk()
    fourth = store.next_chunk()

    assert [first.text, second.text, third.text, fourth.text] == [
        "うん",
        "へえ",
        "ほう",
        "うん",
    ]
    assert first.audio == _wav_bytes("un")
    assert second.audio == _wav_bytes("hee")
    assert third.audio == _wav_bytes("hou")


def test_maai_backchannel_detector_threshold_and_cooldown(tmp_path: Path) -> None:
    _write_assets(tmp_path)
    now = datetime(2026, 6, 20, tzinfo=UTC)
    detector = MaaiBackchannelDetector(
        config=MaaiBackchannelConfig(threshold=0.5, cooldown_ms=1500),
        assets=BackchannelAssetStore(tmp_path),
    )

    assert detector.handle_result({"p_bc_react": 0.49}, observed_at=now) is None

    first = detector.handle_result({"p_bc_react": 0.51}, observed_at=now)
    suppressed = detector.handle_result(
        {"p_bc_react": 0.90},
        observed_at=now + timedelta(milliseconds=500),
    )
    second = detector.handle_result(
        {"p_bc_emo": 0.80},
        observed_at=now + timedelta(milliseconds=1600),
    )

    assert first is not None
    assert first.text == "うん"
    assert first.reason == "p_bc_react_threshold"
    assert suppressed is None
    assert second is not None
    assert second.text == "へえ"
    assert second.reason == "p_bc_emo_threshold"


def test_maai_backchannel_detector_skips_while_playback_active(tmp_path: Path) -> None:
    _write_assets(tmp_path)
    detector = MaaiBackchannelDetector(
        config=MaaiBackchannelConfig(threshold=0.5),
        assets=BackchannelAssetStore(tmp_path),
        playback_active=lambda: True,
    )

    emission = detector.handle_result(
        {"p_bc_react": 0.95},
        observed_at=datetime(2026, 6, 20, tzinfo=UTC),
    )

    assert emission is None


def test_maai_backchannel_detector_buffers_audio_until_frame_size(
    tmp_path: Path,
) -> None:
    class FakeAudioChannel:
        def __init__(self) -> None:
            self.chunks: list[tuple[float, ...]] = []

        def put_chunk(self, chunk: object) -> None:
            self.chunks.append(tuple(float(value) for value in chunk))

    _write_assets(tmp_path)
    detector = MaaiBackchannelDetector(
        config=MaaiBackchannelConfig(),
        assets=BackchannelAssetStore(tmp_path),
    )
    user_channel = FakeAudioChannel()
    silence_channel = FakeAudioChannel()
    detector._audio_ch1 = user_channel
    detector._audio_ch2 = silence_channel

    detector.observe_user_audio(tuple(float(i) for i in range(128)))
    detector.observe_user_audio(tuple(float(i) for i in range(128, 256)))

    assert len(user_channel.chunks) == 1
    assert len(silence_channel.chunks) == 1
    assert user_channel.chunks[0] == tuple(float(i) for i in range(160))
    assert silence_channel.chunks[0] == tuple(0.0 for _ in range(160))

    detector.observe_user_audio(tuple(float(i) for i in range(256, 320)))

    assert len(user_channel.chunks) == 2
    assert user_channel.chunks[1] == tuple(float(i) for i in range(160, 320))


def test_create_backchannel_detector_from_env_is_noop_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_assets(tmp_path)
    monkeypatch.delenv("TOMOKO_V2_MAAI_BACKCHANNEL", raising=False)

    detector = create_backchannel_detector_from_env(asset_dir=tmp_path)

    assert detector is None


def test_create_backchannel_detector_from_env_uses_enabled_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_assets(tmp_path)
    monkeypatch.setenv("TOMOKO_V2_MAAI_BACKCHANNEL", "1")
    monkeypatch.setenv("TOMOKO_V2_MAAI_BACKCHANNEL_THRESHOLD", "0.7")

    detector = create_backchannel_detector_from_env(asset_dir=tmp_path)

    assert detector is not None
    assert detector.config.threshold == pytest.approx(0.7)


def test_maai_result_poll_reads_current_result_queue_without_timeout_kwarg() -> None:
    class FakeMaai:
        def __init__(self) -> None:
            self.result_dict_queue: Queue[dict[str, float]] = Queue()

        def get_result(self, timeout: float | None = None) -> dict[str, float]:
            raise AssertionError("result_dict_queue should be used before get_result")

    maai = FakeMaai()
    maai.result_dict_queue.put({"p_bc_react": 0.8})

    assert _read_maai_result_once(maai) == {"p_bc_react": 0.8}


def test_maai_result_poll_falls_back_when_queue_rejects_timeout_kwarg() -> None:
    class TimeoutlessQueue:
        def __init__(self) -> None:
            self.result = {"p_bc_emo": 0.9}

        def get(self, **kwargs: object) -> dict[str, float]:
            if kwargs:
                raise TypeError("timeout is not supported")
            return self.result

    class FakeMaai:
        def __init__(self) -> None:
            self.result_dict_queue = TimeoutlessQueue()

        def get_result(self) -> dict[str, float]:
            raise AssertionError("queue should still be used")

    assert _read_maai_result_once(FakeMaai()) == {"p_bc_emo": 0.9}
