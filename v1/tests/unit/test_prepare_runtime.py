from __future__ import annotations

from pathlib import Path

import pytest

from _tools import prepare_runtime


@pytest.mark.unit
def test_prepare_runtime_launches_voicevox_and_builds_apple_speech(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(
        tmp_path,
        tts_backend='tts_backend = "voicevox_tsumugi"',
        stt_backend='stt_backend = "local_apple_speech_ja"',
        backends="""
[backends.voicevox_tsumugi]
type = "voicevox"
url = "http://127.0.0.1:50021"
privacy_allowed = true

[backends.local_apple_speech_ja]
type = "apple_speech"
language = "ja-JP"
on_device = true
timeout_s = 30
privacy_allowed = true
""",
    )
    readiness = iter([False, True])
    launched: list[str] = []
    built: list[str] = []

    monkeypatch.setattr(
        prepare_runtime,
        "is_voicevox_ready",
        lambda _url: next(readiness),
    )
    monkeypatch.setattr(
        prepare_runtime,
        "launch_voicevox_app",
        lambda: launched.append("VOICEVOX"),
    )

    class FakeAppleSpeechSTT:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def warm_up(self) -> None:
            built.append("apple_speech")

    monkeypatch.setattr(prepare_runtime, "AppleSpeechSTT", FakeAppleSpeechSTT)

    results = prepare_runtime.prepare_runtime(config_path=config_path, voicevox_wait_s=0.1)

    assert [(result.name, result.status) for result in results] == [
        ("tts", "started"),
        ("stt", "ready"),
    ]
    assert launched == ["VOICEVOX"]
    assert built == ["apple_speech"]


@pytest.mark.unit
def test_prepare_runtime_skips_backends_without_prepare_work(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path,
        tts_backend='tts_backend = "kokoro_mlx"',
        stt_backend='stt_backend = "local_whisper_mlx_small"',
        backends="""
[backends.kokoro_mlx]
type = "kokoro_mlx"
model = "mlx-community/Kokoro-82M-bf16"
privacy_allowed = true

[backends.local_whisper_mlx_small]
type = "mlx_whisper"
model = "mlx-community/whisper-small-mlx"
privacy_allowed = true
""",
    )

    results = prepare_runtime.prepare_runtime(config_path=config_path)

    assert [(result.name, result.status) for result in results] == [
        ("tts", "skip"),
        ("stt", "skip"),
    ]


@pytest.mark.unit
def test_prepare_tts_backend_reports_voicevox_error_when_launch_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prepare_runtime, "is_voicevox_ready", lambda _url: False)
    spec = prepare_runtime.BackendSpec(
        name="voicevox_tsumugi",
        type="voicevox",
        url="http://127.0.0.1:50021",
    )

    result = prepare_runtime.prepare_tts_backend(spec, launch_apps=False)

    assert result.name == "tts"
    assert result.status == "error"
    assert "not responding" in result.detail


@pytest.mark.unit
def test_prepare_tts_backend_reports_voicevox_launch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prepare_runtime, "is_voicevox_ready", lambda _url: False)

    def fail_launch() -> None:
        raise RuntimeError("missing app")

    monkeypatch.setattr(prepare_runtime, "launch_voicevox_app", fail_launch)
    spec = prepare_runtime.BackendSpec(
        name="voicevox_tsumugi",
        type="voicevox",
        url="http://127.0.0.1:50021",
    )

    result = prepare_runtime.prepare_tts_backend(spec)

    assert result.name == "tts"
    assert result.status == "error"
    assert "missing app" in result.detail


def _write_config(
    tmp_path: Path,
    *,
    tts_backend: str,
    stt_backend: str,
    backends: str,
) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[node]
role = "central_realtime"

[database]
dsn = "postgresql://tomoko:tomoko@localhost:5432/tomoko"

[audio]
sample_rate = 16000
chunk_ms = 32
vad_silence_ms = 800

[inference]
conversation_backend = "unused"
{tts_backend}
{stt_backend}

{backends}
""".lstrip()
    )
    return config_path
