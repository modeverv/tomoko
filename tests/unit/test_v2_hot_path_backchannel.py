from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from server.hot_path.backchannel import (
    BackchannelAssetStore,
    MaaiBackchannelConfig,
    MaaiBackchannelDetector,
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
