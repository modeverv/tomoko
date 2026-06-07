from __future__ import annotations

import pytest

from server.edge.main import _warm_up_app, app
from server.gateway.thinking.fast import ThinkFastMode
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


class FakeWarmableTTS:
    def __init__(self) -> None:
        self.warm_up_count = 0

    async def warm_up(self) -> None:
        self.warm_up_count += 1


class FakeWarmableSpeechNormalizer:
    def __init__(self) -> None:
        self.warm_up_count = 0

    async def warm_up(self) -> None:
        self.warm_up_count += 1


class FakeWarmableEmbeddingBackend:
    def __init__(self) -> None:
        self.warm_up_count = 0

    async def warm_up(self) -> None:
        self.warm_up_count += 1


class FakeWarmableConversationBackend:
    name = "fake_conversation"

    def __init__(self) -> None:
        self.warm_up_count = 0
        self.prompt_warm_up_calls: list[tuple[str, list[dict[str, str]], int | None]] = []

    async def warm_up(self) -> None:
        self.warm_up_count += 1

    async def chat_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        trace_role: str | None = None,
    ):
        self.prompt_warm_up_calls.append((system_prompt, messages, max_tokens))
        yield "うん"


class FakeRouter:
    def __init__(
        self,
        backend: FakeWarmableConversationBackend,
        role_backends: dict[str, FakeWarmableConversationBackend] | None = None,
    ) -> None:
        self.backend = backend
        self.role_backends = role_backends or {}
        self.selected_roles: list[str] = []

    async def select(self, role: str, preference: str):
        assert preference == "privacy"
        self.selected_roles.append(role)
        return self.role_backends.get(role, self.backend)


@pytest.mark.unit
async def test_startup_warms_configured_transcriber_tts_and_speech_normalizer() -> None:
    previous_state = dict(app.state._state)
    transcriber = FakeWarmableTranscriber()
    tts = FakeWarmableTTS()
    speech_normalizer = FakeWarmableSpeechNormalizer()
    embedding_backend = FakeWarmableEmbeddingBackend()
    conversation = FakeWarmableConversationBackend()
    config = NodeConfig(
        node=NodeSection(role="edge"),
        inference=InferenceSection(
            conversation_backend="local_qwen7b",
            tts_backend="say",
            stt_backend="local_whisper_mlx_small",
            embedding_backend="local_bge_m3",
        ),
        backends={
            "local_whisper_mlx_small": BackendSpec(
                name="local_whisper_mlx_small",
                type="mlx_whisper",
                model="mlx-community/whisper-small-mlx",
            ),
            "say": BackendSpec(name="say", type="say", voice="Kyoko"),
            "local_bge_m3": BackendSpec(
                name="local_bge_m3",
                type="bge_m3",
                model="BAAI/bge-m3",
                dimensions=1024,
            ),
            "local_qwen7b": BackendSpec(
                name="local_qwen7b",
                type="ollama",
                url="http://localhost:11434",
                model="qwen2.5:7b",
            ),
        },
        audio=AudioSection(sample_rate=16000, chunk_ms=32, vad_silence_ms=400),
        database=DatabaseSection(dsn="postgresql://example"),
    )
    try:
        app.state.config_factory = lambda: config
        app.state.transcriber_factory = lambda: transcriber
        app.state.tts_backend_factory = lambda: tts
        app.state.router_factory = lambda: FakeRouter(conversation)
        app.state.speech_normalizer_factory = lambda: speech_normalizer
        app.state.embedding_backend_factory = lambda: embedding_backend
        app.state.skip_warm_up = False

        await _warm_up_app()

        assert transcriber.warm_up_count == 1
        assert tts.warm_up_count == 1
        assert conversation.warm_up_count == 1
        assert embedding_backend.warm_up_count == 1
        assert speech_normalizer.warm_up_count == 1
    finally:
        app.state._state.clear()
        app.state._state.update(previous_state)


@pytest.mark.unit
async def test_startup_warmup_can_be_skipped() -> None:
    previous_state = dict(app.state._state)
    transcriber = FakeWarmableTranscriber()
    tts = FakeWarmableTTS()
    speech_normalizer = FakeWarmableSpeechNormalizer()
    try:
        app.state.config_factory = lambda: None
        app.state.transcriber_factory = lambda: transcriber
        app.state.tts_backend_factory = lambda: tts
        app.state.speech_normalizer_factory = lambda: speech_normalizer
        app.state.skip_warm_up = True

        await _warm_up_app()

        assert transcriber.warm_up_count == 0
        assert tts.warm_up_count == 0
        assert speech_normalizer.warm_up_count == 0
    finally:
        app.state._state.clear()
        app.state._state.update(previous_state)


@pytest.mark.unit
async def test_startup_skips_speech_normalizer_when_disabled() -> None:
    previous_state = dict(app.state._state)
    transcriber = FakeWarmableTranscriber()
    tts = FakeWarmableTTS()
    speech_normalizer = FakeWarmableSpeechNormalizer()
    conversation = FakeWarmableConversationBackend()
    config = NodeConfig(
        node=NodeSection(role="edge"),
        inference=InferenceSection(
            conversation_backend="local_gemma",
            tts_backend="say",
            stt_backend="local_whisper_mlx_small",
            speech_normalizer_enabled=False,
        ),
        backends={
            "local_whisper_mlx_small": BackendSpec(
                name="local_whisper_mlx_small",
                type="mlx_whisper",
                model="mlx-community/whisper-small-mlx",
            ),
            "say": BackendSpec(name="say", type="say", voice="Kyoko"),
            "local_gemma": BackendSpec(
                name="local_gemma",
                type="gemma_mlx",
                model="mlx-community/gemma-4-e2b-it-4bit",
            ),
        },
        audio=AudioSection(sample_rate=16000, chunk_ms=32, vad_silence_ms=400),
        database=DatabaseSection(dsn="postgresql://example"),
    )
    try:
        app.state.config_factory = lambda: config
        app.state.transcriber_factory = lambda: transcriber
        app.state.tts_backend_factory = lambda: tts
        app.state.router_factory = lambda: FakeRouter(conversation)
        app.state.speech_normalizer_factory = lambda: speech_normalizer
        app.state.skip_warm_up = False

        await _warm_up_app()

        assert transcriber.warm_up_count == 1
        assert tts.warm_up_count == 1
        assert conversation.warm_up_count == 1
        assert speech_normalizer.warm_up_count == 0
    finally:
        app.state._state.clear()
        app.state._state.update(previous_state)


@pytest.mark.unit
async def test_startup_warms_dflash_prompt_prefix_for_unique_no_think_backends(
    tmp_path,
) -> None:
    previous_state = dict(app.state._state)
    transcriber = FakeWarmableTranscriber()
    tts = FakeWarmableTTS()
    embedding_backend = FakeWarmableEmbeddingBackend()
    conversation = FakeWarmableConversationBackend()
    memory = FakeWarmableConversationBackend()
    memory.name = "fake_memory"
    persona = tmp_path / "persona.md"
    persona.write_text("あなたはトモコです。\n固定ペルソナです。", encoding="utf-8")
    overlay = tmp_path / "persona_overlay.md"
    overlay.write_text("追加の話し方です。", encoding="utf-8")
    config = NodeConfig(
        node=NodeSection(role="central_realtime"),
        inference=InferenceSection(
            conversation_backend="dflash_26b",
            session_summary_backend="dflash_26b",
            memory_extraction_backend="dflash_31b",
            persona_update_backend="dflash_31b",
            candidate_gen_backend="dflash_26b",
            diary_backend="dflash_26b",
            tts_backend="say",
            stt_backend="local_whisper_mlx_small",
            embedding_backend="local_bge_m3",
            speech_normalizer_enabled=False,
        ),
        backends={
            "local_whisper_mlx_small": BackendSpec(
                name="local_whisper_mlx_small",
                type="mlx_whisper",
                model="mlx-community/whisper-small-mlx",
            ),
            "say": BackendSpec(name="say", type="say", voice="Kyoko"),
            "local_bge_m3": BackendSpec(
                name="local_bge_m3",
                type="bge_m3",
                model="BAAI/bge-m3",
                dimensions=1024,
            ),
            "dflash_26b": BackendSpec(
                name="dflash_26b",
                type="lm_studio",
                url="http://localhost:8082",
                model="gemma-4-26b-a4b-it-mlx",
                chat_template_kwargs={"enable_thinking": False},
            ),
            "dflash_31b": BackendSpec(
                name="dflash_31b",
                type="lm_studio",
                url="http://localhost:8081",
                model="gemma-4-31b-it-mlx",
                chat_template_kwargs={"enable_thinking": False},
            ),
            "lmstudio_plain": BackendSpec(
                name="lmstudio_plain",
                type="lm_studio",
                url="http://localhost:1234",
                model="gemma-4-e2b-it-mlx",
            ),
        },
        audio=AudioSection(sample_rate=16000, chunk_ms=32, vad_silence_ms=400),
        database=DatabaseSection(dsn="postgresql://example"),
    )
    router = FakeRouter(
        conversation,
        role_backends={
            "conversation": conversation,
            "session_summary": conversation,
            "candidate_gen": conversation,
            "diary": conversation,
            "memory_extraction": memory,
            "persona_update": memory,
        },
    )
    try:
        app.state.config_factory = lambda: config
        app.state.transcriber_factory = lambda: transcriber
        app.state.tts_backend_factory = lambda: tts
        app.state.embedding_backend_factory = lambda: embedding_backend
        app.state.router_factory = lambda: router
        app.state.think_fast_factory = lambda: ThinkFastMode(
            persona_path=persona,
            persona_overlay_path=overlay,
            prompt_log_path=None,
        )
        app.state.skip_warm_up = False

        await _warm_up_app()

        assert len(conversation.prompt_warm_up_calls) == 1
        assert len(memory.prompt_warm_up_calls) == 1
        system_prompt, messages, max_tokens = conversation.prompt_warm_up_calls[0]
        assert "固定ペルソナです" in system_prompt
        assert "追加の話し方です" in system_prompt
        assert messages == [{"role": "user", "content": "起動時 warm-up です。短く返事して。"}]
        assert max_tokens == 4
    finally:
        app.state._state.clear()
        app.state._state.update(previous_state)
