from __future__ import annotations

from server.shared.config import BackendSpec
from server.shared.inference.tts.base import TTSBackend
from server.shared.inference.tts.say import SayBackend


def create_tts_backend(spec: BackendSpec) -> TTSBackend:
    if spec.type == "say":
        return SayBackend.from_spec(spec)
    raise ValueError(f"unsupported TTS backend type: {spec.type}")
