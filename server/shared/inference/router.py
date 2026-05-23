from dataclasses import dataclass
from server.shared.config import NodeConfig
from server.shared.inference.backends.base import InferenceBackend
from server.shared.inference.backends.ollama import OllamaBackend


@dataclass
class InferenceMetrics:
    latency_ms: int


class InferenceRouter:
    def __init__(self, config: NodeConfig, monitor: "MockMonitor | None" = None) -> None:
        self.config = config
        self.monitor = monitor
        self.backends: dict[str, InferenceBackend] = {}

        for name, spec in config.backends.items():
            if spec.type == "ollama":
                if spec.url and spec.model:
                    self.backends[name] = OllamaBackend(
                        name=spec.name,
                        url=spec.url,
                        model=spec.model,
                        privacy_allowed=spec.privacy_allowed,
                    )
            # Future backends (mlx, etc.) will be added here

    async def select(self, role: str, preference: str = "latency") -> InferenceBackend:
        if role == "conversation":
            # For simplicity in Phase 4, return the configured conversation_backend.
            # In the future, preference and monitor might be used to select fallback dynamically.
            backend_name = self.config.inference.conversation_backend
            if backend_name in self.backends:
                return self.backends[backend_name]
            
            # If default not available, try to find another privacy allowed one if privacy preference
            if preference == "privacy":
                for backend in self.backends.values():
                    if backend.privacy_allowed:
                        return backend
            raise ValueError(f"No suitable backend found for role: {role}")
        raise ValueError(f"Unknown role: {role}")
