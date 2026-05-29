import pytest

from server.shared.config import (
    AudioSection,
    BackendSpec,
    DatabaseSection,
    InferenceSection,
    NodeConfig,
    NodeSection,
)
from server.shared.inference.backends.lm_studio import LMStudioBackend
from server.shared.inference.backends.mlx_lm import MLXLMBackend
from server.shared.inference.monitor import MockMonitor
from server.shared.inference.router import InferenceMetrics, InferenceRouter


def make_config(
    *,
    conversation_backend: str = "local",
    conversation_fallback: str | None = "cloud",
    session_summary_backend: str | None = None,
    session_summary_fallback: str | None = None,
    memory_extraction_backend: str | None = None,
    memory_extraction_fallback: str | None = None,
    diary_backend: str | None = None,
    diary_fallback: str | None = None,
) -> NodeConfig:
    return NodeConfig(
        node=NodeSection(role="central_realtime"),
        inference=InferenceSection(
            conversation_backend=conversation_backend,
            conversation_fallback=conversation_fallback,
            session_summary_backend=session_summary_backend,
            session_summary_fallback=session_summary_fallback,
            memory_extraction_backend=memory_extraction_backend,
            memory_extraction_fallback=memory_extraction_fallback,
            diary_backend=diary_backend,
            diary_fallback=diary_fallback,
            stt_backend=None,
            vad_backend=None,
            tts_backend="say",
        ),
        backends={
            "local": BackendSpec(
                name="local",
                type="ollama",
                url="http://localhost:11434",
                model="qwen2.5:7b",
                max_latency_ms=300,
                privacy_allowed=True,
            ),
            "cloud": BackendSpec(
                name="cloud",
                type="ollama",
                url="http://localhost:11434",
                model="cloud-model",
                max_latency_ms=2000,
                privacy_allowed=False,
            ),
            "local_fallback": BackendSpec(
                name="local_fallback",
                type="ollama",
                url="http://localhost:11434",
                model="qwen2.5:7b",
                max_latency_ms=800,
                privacy_allowed=True,
            ),
        },
        audio=AudioSection(sample_rate=16000, chunk_ms=32, vad_silence_ms=400),
        database=DatabaseSection(dsn="postgresql://tomoko:tomoko@localhost:5432/tomoko"),
    )

@pytest.mark.unit
async def test_router_reads_config():
    config = NodeConfig.load("config/central_realtime.toml")
    router = InferenceRouter(config, monitor=MockMonitor())
    backend = await router.select("conversation", "latency")
    assert backend is not None
    assert isinstance(backend, LMStudioBackend)
    assert backend.name == "lmstudio_gemma4_26b_a4b"
    assert backend.model == "gemma-4-26b-a4b-it-mlx"


@pytest.mark.unit
async def test_router_can_select_lfm_mlx_lm_backend() -> None:
    config = make_config(
        conversation_backend="local_lfm25_12b_jp_mlx",
        conversation_fallback=None,
    )
    config.backends["local_lfm25_12b_jp_mlx"] = BackendSpec(
        name="local_lfm25_12b_jp_mlx",
        type="mlx_lm",
        model="lmstudio-community/LFM2.5-1.2B-Instruct-MLX-4bit",
        max_latency_ms=800,
        privacy_allowed=True,
    )
    router = InferenceRouter(config, monitor=MockMonitor())

    backend = await router.select("conversation", "privacy")

    assert isinstance(backend, MLXLMBackend)
    assert backend.name == "local_lfm25_12b_jp_mlx"
    assert backend.model_name == "lmstudio-community/LFM2.5-1.2B-Instruct-MLX-4bit"


@pytest.mark.unit
def test_central_config_contains_lfm_mlx_lm_backend() -> None:
    config = NodeConfig.load("config/central_realtime.toml")

    backend = config.backends["local_lfm25_12b_jp_mlx"]

    assert backend.type == "mlx_lm"
    assert backend.model == "lmstudio-community/LFM2.5-1.2B-Instruct-MLX-4bit"
    assert backend.privacy_allowed

@pytest.mark.unit
async def test_privacy_preference_can_use_private_configured_fallback():
    config = NodeConfig.load("config/central_realtime.toml")
    router = InferenceRouter(
        config=config,
        monitor=MockMonitor({"lmstudio_gemma4_26b_a4b": InferenceMetrics(latency_ms=6000)})
    )
    backend = await router.select("conversation", "privacy")
    assert backend.privacy_allowed
    assert backend.name == "local_gemma4_e2b_mlx"


@pytest.mark.unit
async def test_latency_preference_uses_configured_fallback_when_primary_is_slow():
    config = make_config(conversation_fallback="local_fallback")
    router = InferenceRouter(
        config=config,
        monitor=MockMonitor({"local": InferenceMetrics(latency_ms=600)}),
    )

    backend = await router.select("conversation", "latency")

    assert backend.name == "local_fallback"


@pytest.mark.unit
async def test_privacy_preference_does_not_use_non_private_fallback_when_primary_is_slow():
    router = InferenceRouter(
        config=make_config(conversation_fallback="cloud"),
        monitor=MockMonitor({"local": InferenceMetrics(latency_ms=600)}),
    )

    backend = await router.select("conversation", "privacy")

    assert backend.name == "local"


@pytest.mark.unit
async def test_session_summary_role_uses_configured_backend_and_fallback() -> None:
    config = make_config(
        session_summary_backend="local",
        session_summary_fallback="local_fallback",
    )
    router = InferenceRouter(
        config=config,
        monitor=MockMonitor({"local": InferenceMetrics(latency_ms=600)}),
    )

    backend = await router.select("session_summary", "privacy")

    assert backend.name == "local_fallback"


@pytest.mark.unit
async def test_memory_extraction_role_uses_configured_backend_and_fallback() -> None:
    config = make_config(
        memory_extraction_backend="local",
        memory_extraction_fallback="local_fallback",
    )
    router = InferenceRouter(
        config=config,
        monitor=MockMonitor({"local": InferenceMetrics(latency_ms=600)}),
    )

    backend = await router.select("memory_extraction", "privacy")

    assert backend.name == "local_fallback"


@pytest.mark.unit
async def test_diary_role_uses_configured_backend_and_fallback() -> None:
    config = make_config(
        diary_backend="local",
        diary_fallback="local_fallback",
    )
    router = InferenceRouter(
        config=config,
        monitor=MockMonitor({"local": InferenceMetrics(latency_ms=600)}),
    )

    backend = await router.select("diary", "privacy")

    assert backend.name == "local_fallback"
