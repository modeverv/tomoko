from __future__ import annotations

from pathlib import Path

import pytest

CLIENT_DIR = Path("client")
INDEX_HTML = (CLIENT_DIR / "index.html").read_text()
MAIN_JS = (CLIENT_DIR / "main.js").read_text()


@pytest.mark.unit
def test_client_exposes_audio_device_selects() -> None:
    for expected in [
        'id="audio-input-device"',
        'id="audio-output-device"',
        'id="device-status"',
    ]:
        assert expected in INDEX_HTML


@pytest.mark.unit
def test_client_applies_selected_input_device_to_get_user_media() -> None:
    assert "selectedAudioInputDeviceId" in MAIN_JS
    assert "deviceId" in MAIN_JS
    assert "getUserMedia(audioConstraints())" in MAIN_JS


@pytest.mark.unit
def test_client_applies_selected_output_device_when_supported() -> None:
    assert "selectedAudioOutputDeviceId" in MAIN_JS
    assert "setSinkId" in MAIN_JS
    assert "createMediaStreamDestination" in MAIN_JS


@pytest.mark.unit
def test_client_refreshes_device_labels_after_permission() -> None:
    assert "enumerateDevices" in MAIN_JS
    assert "audioinput" in MAIN_JS
    assert "audiooutput" in MAIN_JS
