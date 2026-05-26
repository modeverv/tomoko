from __future__ import annotations

from server.shared.config import BackendSpec
from server.shared.inference.tts.base import TTSBackend
from server.shared.inference.tts.irodori_mlx import IrodoriMLXBackend
from server.shared.inference.tts.irodori_mlx_stream import IrodoriMLXStreamBackend
from server.shared.inference.tts.kokoro_coreml import KokoroCoreMLBackend
from server.shared.inference.tts.kokoro_mlx import KokoroMLXBackend
from server.shared.inference.tts.qwen3_mlx import Qwen3MLXTTSBackend
from server.shared.inference.tts.say import SayBackend
from server.shared.inference.tts.supertonic_coreml import SupertonicCoreMLBackend
from server.shared.inference.tts.voicevox import VoicevoxBackend, VoicevoxStreamBackend


def create_tts_backend(spec: BackendSpec) -> TTSBackend:
    if spec.type == "say":
        return SayBackend.from_spec(spec)
    if spec.type == "kokoro_mlx":
        return KokoroMLXBackend.from_spec(spec)
    if spec.type == "kokoro_coreml":
        return KokoroCoreMLBackend.from_spec(spec)
    if spec.type == "irodori_mlx":
        return IrodoriMLXBackend.from_spec(spec)
    if spec.type == "irodori_mlx_stream":
        return IrodoriMLXStreamBackend.from_spec(spec)
    if spec.type == "qwen3_mlx":
        return Qwen3MLXTTSBackend.from_spec(spec)
    if spec.type == "supertonic_coreml":
        return SupertonicCoreMLBackend.from_spec(spec)
    if spec.type == "voicevox":
        return VoicevoxBackend.from_spec(spec)
    if spec.type == "voicevox_stream":
        return VoicevoxStreamBackend.from_spec(spec)
    raise ValueError(f"unsupported TTS backend type: {spec.type}")
