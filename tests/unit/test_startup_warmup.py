from __future__ import annotations

import pytest

from server.edge.main import _warm_up_app, app
from server.shared.config import (
    AudioSection,
    BackendSpec,
    DatabaseSection,
    InferenceSection,
    NodeConfig,
    NodeSection,
)


class FakeWarmableTranscriber:
    def __init__(self) -> None:
        self.warm_up_count = 0

    async def warm_up(self) -> None:
        self.warm_up_count += 1


@pytest.mark.unit
async def test_startup_warms_configured_transcriber() -> None:
    previous_state = dict(app.state._state)
    transcriber = FakeWarmableTranscriber()
    config = NodeConfig(
        node=NodeSection(role="edge"),
        inference=InferenceSection(
            conversation_backend="local_qwen7b",
            tts_backend="say",
            stt_backend="local_whisper_mlx_small",
        ),
        backends={
            "local_whisper_mlx_small": BackendSpec(
                name="local_whisper_mlx_small",
                type="mlx_whisper",
                model="mlx-community/whisper-small-mlx",
            )
        },
        audio=AudioSection(sample_rate=16000, chunk_ms=32, vad_silence_ms=400),
        database=DatabaseSection(dsn="postgresql://example"),
    )
    try:
        app.state.config_factory = lambda: config
        app.state.transcriber_factory = lambda: transcriber
        app.state.skip_warm_up = False

        await _warm_up_app()

        assert transcriber.warm_up_count == 1
    finally:
        app.state._state.clear()
        app.state._state.update(previous_state)


@pytest.mark.unit
async def test_startup_warmup_can_be_skipped() -> None:
    previous_state = dict(app.state._state)
    transcriber = FakeWarmableTranscriber()
    try:
        app.state.config_factory = lambda: None
        app.state.transcriber_factory = lambda: transcriber
        app.state.skip_warm_up = True

        await _warm_up_app()

        assert transcriber.warm_up_count == 0
    finally:
        app.state._state.clear()
        app.state._state.update(previous_state)
