from __future__ import annotations

import asyncio
import io
import wave
from collections.abc import Callable
from typing import Any

import numpy as np

from server.shared.config import BackendSpec
from server.shared.inference.tts.base import TTSBackend
from server.shared.models import AudioChunkOut, TTSInput

ModelFactory = Callable[[str], Any]


class IrodoriMLXBackend(TTSBackend):
    name = "irodori_mlx"

    STYLE_TO_DURATION_SCALE = {
        "neutral": 1.0,
        "happy": 0.95,
        "surprised": 0.95,
        "excited": 0.9,
        "sad": 1.08,
        "thinking": 1.04,
        "gentle": 1.06,
    }

    def __init__(
        self,
        *,
        model: str = "mlx-community/Irodori-TTS-500M-v3-8bit",
        voice: str = "none",
        num_steps: int = 24,
        t_schedule_mode: str = "sway",
        sway_coeff: float = -1.0,
        model_factory: ModelFactory | None = None,
        loaded_model: Any | None = None,
    ) -> None:
        self.model_name = model
        self.voice = voice
        self.num_steps = num_steps
        self.t_schedule_mode = t_schedule_mode
        self.sway_coeff = sway_coeff
        self._model_factory = model_factory or _load_mlx_audio_model
        self._model = loaded_model

    @classmethod
    def from_spec(cls, spec: BackendSpec) -> IrodoriMLXBackend:
        return cls(
            model=spec.model or "mlx-community/Irodori-TTS-500M-v3-8bit",
            voice=spec.voice or "none",
        )

    async def synthesize(self, tts_input: TTSInput):
        text = tts_input.text.strip()
        if not text:
            return

        audio, sample_rate = await asyncio.to_thread(self._generate_audio, text, tts_input)
        yield AudioChunkOut(
            data=_wav_bytes_from_audio(audio, sample_rate=sample_rate),
            sequence=0,
            is_last=True,
        )

    async def warm_up(self) -> None:
        async for _ in self.synthesize(TTSInput(text="あ。", style="neutral")):
            return

    def _generate_audio(self, text: str, tts_input: TTSInput) -> tuple[np.ndarray, int]:
        model = self._load_model()
        ref_audio = _voice_to_ref_audio(tts_input.voice or self.voice)
        results = model.generate(
            text=text,
            ref_audio=ref_audio,
            duration_scale=self.STYLE_TO_DURATION_SCALE.get(tts_input.style, 1.0),
            num_steps=self.num_steps,
            t_schedule_mode=self.t_schedule_mode,
            sway_coeff=self.sway_coeff,
        )
        result = next(iter(results))
        sample_rate = int(getattr(result, "sample_rate", self._model_sample_rate()))
        return _audio_to_numpy(result.audio), sample_rate

    def _load_model(self) -> Any:
        if self._model is None:
            self._model = self._model_factory(self.model_name)
        return self._model

    def _model_sample_rate(self) -> int:
        model = self._load_model()
        return int(getattr(model, "sample_rate", 48000))


def _load_mlx_audio_model(model_name: str) -> Any:
    try:
        from mlx_audio.tts.utils import load_model
    except ImportError as e:
        raise RuntimeError(
            "mlx-audio is not installed. Install the GitHub version with "
            "`uv add 'mlx-audio @ git+https://github.com/Blaizzy/mlx-audio.git'`."
        ) from e
    return load_model(model_name)


def _voice_to_ref_audio(voice: str | None) -> str | None:
    if voice is None:
        return None
    stripped = voice.strip()
    if not stripped or stripped.lower() in {"none", "default"}:
        return None
    return stripped


def _audio_to_numpy(audio: Any) -> np.ndarray:
    if hasattr(audio, "tolist"):
        audio = np.array(audio.tolist(), dtype=np.float32)
    else:
        audio = np.asarray(audio, dtype=np.float32)
    return audio


def _wav_bytes_from_audio(audio: np.ndarray, *, sample_rate: int) -> bytes:
    if audio.ndim > 1:
        audio = np.mean(audio, axis=0 if audio.shape[0] <= 2 else 1)
    audio = np.asarray(audio, dtype=np.float32)
    audio = np.nan_to_num(audio, nan=0.0, posinf=1.0, neginf=-1.0)
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")

    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())
    return output.getvalue()
