from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from server.shared.config import NodeConfig
from server.shared.inference.backends.base import InferenceBackend
from server.shared.inference.backends.gemma_mlx import GemmaMLXBackend
from server.shared.inference.backends.lm_studio import LMStudioBackend
from server.shared.inference.backends.mlx_lm import MLXLMBackend
from server.shared.inference.backends.ollama import OllamaBackend


@dataclass
class InferenceMetrics:
    latency_ms: int


class InferenceRouter:
    def __init__(self, config: NodeConfig, monitor: Any | None = None) -> None:
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
            elif spec.type == "gemma_mlx":
                if spec.model:
                    self.backends[name] = GemmaMLXBackend(
                        name=spec.name,
                        model=spec.model,
                        privacy_allowed=spec.privacy_allowed,
                    )
            elif spec.type == "lm_studio":
                if spec.url and spec.model:
                    self.backends[name] = LMStudioBackend(
                        name=spec.name,
                        url=spec.url,
                        model=spec.model,
                        privacy_allowed=spec.privacy_allowed,
                    )
            elif spec.type == "mlx_lm":
                if spec.model:
                    self.backends[name] = MLXLMBackend(
                        name=spec.name,
                        model=spec.model,
                        privacy_allowed=spec.privacy_allowed,
                    )

    async def select(self, role: str, preference: str = "latency") -> InferenceBackend:
        if role == "conversation":
            backend_name = self.config.inference.conversation_backend
            backend = self._get_backend(backend_name, role, preference)
            fallback = await self._fallback_if_needed(
                backend_name,
                preference,
                fallback_name=self.config.inference.conversation_fallback,
            )
            return fallback or backend
        if role == "session_summary":
            backend_name = (
                self.config.inference.session_summary_backend
                or self.config.inference.conversation_backend
            )
            backend = self._get_backend(backend_name, role, preference)
            fallback = await self._fallback_if_needed(
                backend_name,
                preference,
                fallback_name=self.config.inference.session_summary_fallback,
            )
            return fallback or backend
        if role == "memory_extraction":
            backend_name = (
                self.config.inference.memory_extraction_backend
                or self.config.inference.session_summary_backend
                or self.config.inference.conversation_backend
            )
            backend = self._get_backend(backend_name, role, preference)
            fallback = await self._fallback_if_needed(
                backend_name,
                preference,
                fallback_name=self.config.inference.memory_extraction_fallback,
            )
            return fallback or backend
        if role == "candidate_gen":
            backend_name = (
                self.config.inference.candidate_gen_backend
                or self.config.inference.session_summary_backend
                or self.config.inference.conversation_backend
            )
            backend = self._get_backend(backend_name, role, preference)
            fallback = await self._fallback_if_needed(
                backend_name,
                preference,
                fallback_name=self.config.inference.candidate_gen_fallback,
            )
            return fallback or backend
        if role == "diary":
            backend_name = (
                self.config.inference.diary_backend
                or self.config.inference.session_summary_backend
                or self.config.inference.conversation_backend
            )
            backend = self._get_backend(backend_name, role, preference)
            fallback = await self._fallback_if_needed(
                backend_name,
                preference,
                fallback_name=self.config.inference.diary_fallback,
            )
            return fallback or backend
        raise ValueError(f"Unknown role: {role}")

    def _get_backend(
        self, backend_name: str, role: str, preference: str
    ) -> InferenceBackend:
        if backend_name in self.backends:
            backend = self.backends[backend_name]
            if preference == "privacy" and not backend.privacy_allowed:
                for candidate in self.backends.values():
                    if candidate.privacy_allowed:
                        return candidate
            return backend
        raise ValueError(f"No suitable backend found for role: {role}")

    async def _fallback_if_needed(
        self,
        backend_name: str,
        preference: str,
        fallback_name: str | None,
    ) -> InferenceBackend | None:
        if self.monitor is None:
            return None

        spec = self.config.backends[backend_name]
        if spec.max_latency_ms is None:
            return None

        metrics = await self.monitor.latest(backend_name)
        if metrics is None:
            return None
        if metrics.latency_ms is not None and metrics.latency_ms <= spec.max_latency_ms:
            return None

        if fallback_name is None or fallback_name not in self.backends:
            return None

        fallback = self.backends[fallback_name]
        if preference == "privacy" and not fallback.privacy_allowed:
            return None
        return fallback
