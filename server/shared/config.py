from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class NodeSection:
    role: str
    device_id: str | None = None
    gateway_ws_url: str | None = None


@dataclass(frozen=True)
class InferenceSection:
    conversation_backend: str
    tts_backend: str
    stt_backend: str | None = None
    vad_backend: str | None = None
    embedding_backend: str | None = None
    session_summary_backend: str | None = None
    candidate_gen_backend: str | None = None
    diary_backend: str | None = None
    conversation_fallback: str | None = None
    session_summary_fallback: str | None = None
    candidate_gen_fallback: str | None = None
    diary_fallback: str | None = None
    speech_normalizer_enabled: bool = True


@dataclass(frozen=True)
class BackendSpec:
    name: str
    type: str
    model: str | None = None
    model_path: str | None = None
    command: str | None = None
    url: str | None = None
    voice: str | None = None
    sample_rate: int | None = None
    dimensions: int | None = None
    language: str | None = None
    total_step: int | None = None
    speed: float | None = None
    compute_units: str | None = None
    max_latency_ms: int | None = None
    privacy_allowed: bool = True
    streaming: bool = False
    stream_interval_ms: int = 1000
    stream_min_audio_ms: int = 1000


@dataclass(frozen=True)
class AudioSection:
    sample_rate: int
    chunk_ms: int
    vad_silence_ms: int


@dataclass(frozen=True)
class DatabaseSection:
    dsn: str


@dataclass(frozen=True)
class NodeConfig:
    node: NodeSection
    inference: InferenceSection
    backends: dict[str, BackendSpec]
    audio: AudioSection
    database: DatabaseSection

    @classmethod
    def load(cls, path: str | Path) -> NodeConfig:
        data = tomllib.loads(Path(path).read_text())

        backends = {
            name: BackendSpec(name=name, **spec)
            for name, spec in data.get("backends", {}).items()
        }

        return cls(
            node=NodeSection(**data["node"]),
            inference=InferenceSection(**data["inference"]),
            backends=backends,
            audio=AudioSection(**data["audio"]),
            database=DatabaseSection(**data["database"]),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "inference": self.inference,
            "backends": self.backends,
            "audio": self.audio,
            "database": self.database,
        }
