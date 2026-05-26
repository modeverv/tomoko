from __future__ import annotations

import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pytest

from server.edge.pipeline.stt import (
    AppleSpeechSTT,
    FasterWhisperSTT,
    MlxWhisperSTT,
    WhisperCoreMLSTT,
    WhisperKitServeSTT,
    create_stt_transcriber,
)
from server.shared.config import BackendSpec
from server.shared.models import SpeechSegment


@pytest.mark.unit
def test_create_stt_transcriber_supports_mlx_whisper() -> None:
    transcriber = create_stt_transcriber(
        BackendSpec(
            name="local_whisper_mlx_small",
            type="mlx_whisper",
            model="mlx-community/whisper-small-mlx",
            streaming=True,
            stream_interval_ms=500,
            stream_min_audio_ms=500,
        )
    )

    assert isinstance(transcriber, MlxWhisperSTT)
    assert transcriber.model_name == "mlx-community/whisper-small-mlx"
    assert transcriber.streaming is True


@pytest.mark.unit
def test_create_stt_transcriber_supports_whisper_coreml() -> None:
    transcriber = create_stt_transcriber(
        BackendSpec(
            name="local_whisper_coreml_small",
            type="whisper_coreml",
            model_path="models/whisper/ggml-small.bin",
            command="whisper-cli",
            streaming=True,
            stream_interval_ms=500,
            stream_min_audio_ms=500,
        )
    )

    assert isinstance(transcriber, WhisperCoreMLSTT)
    assert transcriber.model_path == "models/whisper/ggml-small.bin"
    assert transcriber.streaming is True


@pytest.mark.unit
def test_create_stt_transcriber_supports_whisperkit_serve() -> None:
    transcriber = create_stt_transcriber(
        BackendSpec(
            name="local_whisperkit_serve_large_turbo_632m_cpu_ne",
            type="whisperkit_serve",
            url="http://127.0.0.1:50062",
            model="large-v3-v20240930_turbo_632MB",
            command="whisperkit-cli",
            compute_units="cpuAndNeuralEngine",
            streaming=True,
            stream_interval_ms=500,
            stream_min_audio_ms=500,
        )
    )

    assert isinstance(transcriber, WhisperKitServeSTT)
    assert transcriber.url == "http://127.0.0.1:50062"
    assert transcriber.model_name == "large-v3-v20240930_turbo_632MB"
    assert transcriber.compute_units == "cpuAndNeuralEngine"
    assert transcriber.streaming is True


@pytest.mark.unit
def test_create_stt_transcriber_supports_apple_speech() -> None:
    transcriber = create_stt_transcriber(
        BackendSpec(
            name="local_apple_speech_ja",
            type="apple_speech",
            language="ja-JP",
            on_device=True,
            timeout_s=12.0,
        )
    )

    assert isinstance(transcriber, AppleSpeechSTT)
    assert transcriber.language == "ja-JP"
    assert transcriber.on_device is True
    assert transcriber.timeout_s == 12.0


@pytest.mark.unit
async def test_mlx_whisper_transcribes_via_temp_wav(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_transcribe(audio_path: str, **kwargs: object) -> dict[str, str]:
        calls.append({"audio_path": audio_path, **kwargs})
        return {"text": "ともこ、聞こえます"}

    monkeypatch.setitem(
        sys.modules,
        "mlx_whisper",
        SimpleNamespace(transcribe=fake_transcribe),
    )
    transcriber = MlxWhisperSTT(model_name="mlx-community/whisper-small-mlx")
    segment = SpeechSegment(
        audio=np.zeros(1600, dtype=np.float32),
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        device_id="local",
        vad_confidence=0.9,
    )

    transcript = await transcriber.transcribe(segment)

    assert transcript.text == "ともこ、聞こえます"
    assert calls[0]["path_or_hf_repo"] == "mlx-community/whisper-small-mlx"
    assert calls[0]["language"] == "ja"
    assert calls[0]["initial_prompt"] == "ともこ"


@pytest.mark.unit
async def test_apple_speech_transcribes_via_sidecar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    class FakeCompleted:
        stdout = '{"text":"ともこ、聞こえます","locale":"ja-JP","onDevice":true,"elapsedMs":42.0}\n'

    def fake_run(args: list[str], **kwargs: object) -> FakeCompleted:
        calls.append(args)
        assert kwargs["check"] is True
        return FakeCompleted()

    monkeypatch.setattr("server.edge.pipeline.stt_apple.subprocess.run", fake_run)
    transcriber = AppleSpeechSTT(command="/bin/echo", language="ja-JP")
    segment = SpeechSegment(
        audio=np.zeros(1600, dtype=np.float32),
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        device_id="local",
        vad_confidence=0.9,
    )

    transcript = await transcriber.transcribe(segment)

    assert transcript.text == "ともこ、聞こえます"
    assert calls[0][0] == "/bin/echo"
    assert "--audio" in calls[0]
    assert calls[0][calls[0].index("--locale") + 1] == "ja-JP"
    assert "--on-device" in calls[0]


@pytest.mark.unit
async def test_whisper_coreml_transcribes_via_whisper_cpp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    class FakeCompleted:
        stdout = "[00:00:00.000 --> 00:00:01.000] ともこ、聞こえます\n"
        stderr = ""

    def fake_run(args: list[str], **kwargs: object) -> FakeCompleted:
        calls.append(args)
        assert kwargs["check"] is True
        return FakeCompleted()

    monkeypatch.setattr(
        "server.edge.pipeline.stt_coreml.shutil.which",
        lambda _command: "/bin/fake",
    )
    monkeypatch.setattr("server.edge.pipeline.stt_coreml.subprocess.run", fake_run)
    transcriber = WhisperCoreMLSTT(
        model_path="models/whisper/ggml-small.bin",
        command="whisper-cli",
    )
    segment = SpeechSegment(
        audio=np.zeros(1600, dtype=np.float32),
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        device_id="local",
        vad_confidence=0.9,
    )

    transcript = await transcriber.transcribe(segment)

    assert transcript.text == "ともこ、聞こえます"
    assert calls[0][:3] == ["whisper-cli", "-m", "models/whisper/ggml-small.bin"]


@pytest.mark.unit
async def test_whisper_coreml_supports_whisperkit_cli_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    class FakeCompleted:
        stdout = "ともこ、聞こえます\n"
        stderr = ""

    def fake_run(args: list[str], **kwargs: object) -> FakeCompleted:
        calls.append(args)
        assert kwargs["check"] is True
        return FakeCompleted()

    monkeypatch.setattr(
        "server.edge.pipeline.stt_coreml.shutil.which",
        lambda _command: "/bin/fake",
    )
    monkeypatch.setattr("server.edge.pipeline.stt_coreml.subprocess.run", fake_run)
    transcriber = WhisperCoreMLSTT(model_path="small", command="whisperkit-cli")
    segment = SpeechSegment(
        audio=np.zeros(1600, dtype=np.float32),
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        device_id="local",
        vad_confidence=0.9,
    )

    transcript = await transcriber.transcribe(segment)

    assert transcript.text == "ともこ、聞こえます"
    assert calls[0][0:2] == ["whisperkit-cli", "transcribe"]
    assert "--model" in calls[0]


class FakeHTTPResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: dict[str, object] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict[str, object]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"unexpected status: {self.status_code}")


class FakeWhisperKitClient:
    def __init__(self, health_statuses: list[int] | None = None) -> None:
        self._health_statuses = health_statuses or [200]
        self.get_calls: list[str] = []
        self.post_calls: list[dict[str, object]] = []

    async def get(self, url: str) -> FakeHTTPResponse:
        self.get_calls.append(url)
        status = self._health_statuses.pop(0) if self._health_statuses else 200
        return FakeHTTPResponse(status_code=status, payload={"status": "ok"})

    async def post(
        self,
        url: str,
        *,
        files: dict[str, object],
        data: dict[str, object],
    ) -> FakeHTTPResponse:
        self.post_calls.append({"url": url, "files": files, "data": data})
        return FakeHTTPResponse(payload={"text": "ともこ、聞こえます"})

    async def aclose(self) -> None:
        return None


@pytest.mark.unit
async def test_whisperkit_serve_posts_audio_to_transcription_endpoint() -> None:
    client = FakeWhisperKitClient()
    transcriber = WhisperKitServeSTT(
        url="http://127.0.0.1:50060",
        model_name="small",
        client=client,
    )
    segment = SpeechSegment(
        audio=np.zeros(1600, dtype=np.float32),
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        device_id="local",
        vad_confidence=0.9,
    )

    transcript = await transcriber.transcribe(segment)

    assert transcript.text == "ともこ、聞こえます"
    assert client.get_calls == ["http://127.0.0.1:50060/health"]
    assert client.post_calls[0]["url"] == "http://127.0.0.1:50060/v1/audio/transcriptions"
    assert client.post_calls[0]["data"] == {
        "model": "small",
        "language": "ja",
        "prompt": "ともこ",
    }


@pytest.mark.unit
async def test_whisperkit_serve_starts_process_when_healthcheck_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen_calls: list[list[str]] = []

    class FakeProcess:
        returncode = None

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            return None

        def wait(self, timeout: int) -> None:
            del timeout
            return None

    def fake_popen(args: list[str], **kwargs: object) -> FakeProcess:
        del kwargs
        popen_calls.append(args)
        return FakeProcess()

    monkeypatch.setattr(
        "server.edge.pipeline.stt_whisperkit.shutil.which",
        lambda _command: "/bin/fake",
    )
    monkeypatch.setattr("server.edge.pipeline.stt_whisperkit.subprocess.Popen", fake_popen)
    client = FakeWhisperKitClient(health_statuses=[503, 200, 200])
    transcriber = WhisperKitServeSTT(
        url="http://127.0.0.1:50061",
        model_name="small",
        compute_units="cpuAndNeuralEngine",
        client=client,
    )

    await transcriber.warm_up()

    assert popen_calls[0] == [
        "whisperkit-cli",
        "serve",
        "--model",
        "small",
        "--language",
        "ja",
        "--prompt",
        "ともこ",
        "--without-timestamps",
        "--host",
        "127.0.0.1",
        "--port",
        "50061",
        "--audio-encoder-compute-units",
        "cpuAndNeuralEngine",
        "--text-decoder-compute-units",
        "cpuAndNeuralEngine",
    ]


@pytest.mark.unit
async def test_faster_whisper_warm_up_is_noop() -> None:
    transcriber = FasterWhisperSTT.__new__(FasterWhisperSTT)

    await transcriber.warm_up()


@pytest.mark.unit
async def test_mlx_whisper_warm_up_runs_one_transcription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_transcribe(audio_path: str, **kwargs: object) -> dict[str, str]:
        calls.append({"audio_path": audio_path, **kwargs})
        return {"text": ""}

    monkeypatch.setitem(
        sys.modules,
        "mlx_whisper",
        SimpleNamespace(transcribe=fake_transcribe),
    )
    transcriber = MlxWhisperSTT(streaming=True)
    transcriber._stream_buffer = [np.ones(2, dtype=np.float32)]
    transcriber._stream_samples = 2
    transcriber._stream_samples_since_emit = 2
    transcriber._last_stream_text = "old"

    await transcriber.warm_up()

    assert len(calls) == 1
    assert calls[0]["path_or_hf_repo"] == "mlx-community/whisper-small-mlx"
    assert transcriber._stream_buffer == []
    assert transcriber._stream_samples == 0
    assert transcriber._stream_samples_since_emit == 0
    assert transcriber._last_stream_text == ""


@pytest.mark.unit
async def test_mlx_whisper_streaming_returns_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_transcribe(audio_path: str, **kwargs: object) -> dict[str, str]:
        del audio_path, kwargs
        return {"text": "途中です"}

    monkeypatch.setitem(
        sys.modules,
        "mlx_whisper",
        SimpleNamespace(transcribe=fake_transcribe),
    )
    transcriber = MlxWhisperSTT(
        streaming=True,
        stream_interval_ms=500,
        stream_min_audio_ms=500,
    )

    partial = await transcriber.process_stream_chunk(
        np.ones(2, dtype=np.float32),
        device_id="local",
        sample_rate=4,
    )

    assert partial is not None
    assert partial.text == "途中です"
    assert partial.is_final is False


@pytest.mark.unit
async def test_mlx_whisper_streaming_suppresses_duplicate_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_transcribe(audio_path: str, **kwargs: object) -> dict[str, str]:
        del audio_path, kwargs
        return {"text": "途中です"}

    monkeypatch.setitem(
        sys.modules,
        "mlx_whisper",
        SimpleNamespace(transcribe=fake_transcribe),
    )
    transcriber = MlxWhisperSTT(
        streaming=True,
        stream_interval_ms=500,
        stream_min_audio_ms=500,
    )

    first = await transcriber.process_stream_chunk(
        np.ones(2, dtype=np.float32),
        device_id="local",
        sample_rate=4,
    )
    second = await transcriber.process_stream_chunk(
        np.ones(2, dtype=np.float32),
        device_id="local",
        sample_rate=4,
    )

    assert first is not None
    assert second is None
