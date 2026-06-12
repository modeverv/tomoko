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
    memory_extraction_backend: str | None = None
    persona_update_backend: str | None = None
    conversation_fallback: str | None = None
    session_summary_fallback: str | None = None
    candidate_gen_fallback: str | None = None
    diary_fallback: str | None = None
    memory_extraction_fallback: str | None = None
    persona_update_fallback: str | None = None
    speech_normalizer_enabled: bool = True
    skip_base_persona: bool = False


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
    on_device: bool = True
    timeout_s: float | None = None
    max_latency_ms: int | None = None
    privacy_allowed: bool = True
    streaming: bool = False
    stream_interval_ms: int = 1000
    stream_min_audio_ms: int = 1000
    chunk_min_accent_phrases: int | None = None
    segment_length: float | None = None
    chat_template_kwargs: dict[str, Any] | None = None
    adapter_path: str | None = None


@dataclass(frozen=True)
class AudioSection:
    sample_rate: int
    chunk_ms: int
    vad_silence_ms: int
    vad_pre_roll_ms: int = 500
    vap_hybrid_enabled: bool = False
    vap_hybrid_min_silence_ms: int = 150
    vap_hybrid_delta_silence_ms: int = 650
    vap_hybrid_max_silence_ms: int = 800
    vap_hybrid_threshold_probability: float = 0.90


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

        audio_data = dict(data["audio"])
        if (
            "vap_hybrid_max_silence_ms" in audio_data
            and "vap_hybrid_delta_silence_ms" not in audio_data
        ):
            min_s = audio_data.get("vap_hybrid_min_silence_ms", 150)
            max_s = audio_data["vap_hybrid_max_silence_ms"]
            audio_data["vap_hybrid_delta_silence_ms"] = max_s - min_s
        elif (
            "vap_hybrid_delta_silence_ms" in audio_data
            and "vap_hybrid_max_silence_ms" not in audio_data
        ):
            min_s = audio_data.get("vap_hybrid_min_silence_ms", 150)
            delta_s = audio_data["vap_hybrid_delta_silence_ms"]
            audio_data["vap_hybrid_max_silence_ms"] = min_s + delta_s

        return cls(
            node=NodeSection(**data["node"]),
            inference=InferenceSection(**data["inference"]),
            backends=backends,
            audio=AudioSection(**audio_data),
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
