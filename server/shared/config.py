from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class NodeSection:
    role: str
    device_id: str | None = None


@dataclass(frozen=True)
class InferenceSection:
    conversation_backend: str
    tts_backend: str
    stt_backend: str | None = None
    vad_backend: str | None = None
    conversation_fallback: str | None = None


@dataclass(frozen=True)
class BackendSpec:
    name: str
    type: str
    model: str | None = None
    url: str | None = None
    voice: str | None = None
    max_latency_ms: int | None = None
    privacy_allowed: bool = True


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
