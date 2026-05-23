import pytest

from server.shared.config import NodeConfig
from server.shared.inference.router import InferenceMetrics, InferenceRouter
from server.shared.inference.monitor import MockMonitor

@pytest.mark.unit
async def test_router_reads_config():
    config = NodeConfig.load("config/central_realtime.toml")
    router = InferenceRouter(config, monitor=MockMonitor())
    backend = await router.select("conversation", "latency")
    assert backend is not None
    assert backend.name == "local_qwen7b"

@pytest.mark.unit
async def test_privacy_stays_local():
    config = NodeConfig.load("config/central_realtime.toml")
    router = InferenceRouter(
        config=config,
        monitor=MockMonitor({"local_qwen7b": InferenceMetrics(latency_ms=600)})
    )
    backend = await router.select("conversation", "privacy")
    assert backend.privacy_allowed == True
