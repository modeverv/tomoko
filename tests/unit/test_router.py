import pytest

from server.shared.config import (
    AudioSection,
    BackendSpec,
    DatabaseSection,
    InferenceSection,
    NodeConfig,
    NodeSection,
)
from server.shared.inference.monitor import MockMonitor
from server.shared.inference.router import InferenceMetrics, InferenceRouter


def make_config(
    *,
    conversation_backend: str = "local",
    conversation_fallback: str | None = "cloud",
    session_summary_backend: str | None = None,
    session_summary_fallback: str | None = None,
) -> NodeConfig:
    return NodeConfig(
        node=NodeSection(role="central_realtime"),
        inference=InferenceSection(
            conversation_backend=conversation_backend,
            conversation_fallback=conversation_fallback,
            session_summary_backend=session_summary_backend,
            session_summary_fallback=session_summary_fallback,
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
    assert backend.name == "lmstudio_gemma4_e2b"

@pytest.mark.unit
async def test_privacy_preference_can_use_private_configured_fallback():
    config = NodeConfig.load("config/central_realtime.toml")
    router = InferenceRouter(
        config=config,
        monitor=MockMonitor({"lmstudio_gemma4_e2b": InferenceMetrics(latency_ms=900)})
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
