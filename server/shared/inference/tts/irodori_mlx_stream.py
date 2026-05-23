from __future__ import annotations

import asyncio
import re
from typing import Any

import numpy as np

from server.shared.config import BackendSpec
from server.shared.inference.tts.base import TTSBackend
from server.shared.inference.tts.irodori_mlx import (
    IrodoriMLXBackend,
    ModelFactory,
    _audio_to_numpy,
    _load_mlx_audio_model,
    _voice_to_ref_audio,
    _wav_bytes_from_audio,
)
from server.shared.models import AudioChunkOut, TTSInput

_PUNCTUATION = set("。！？!?")
_SOFT_BOUNDARY = set("、，, ")
_COUNTABLE_RE = re.compile(r"[^\s、。，,.！？!?]")
_DURATION_QUALITY_SCALE = 1.5


class IrodoriMLXStreamBackend(TTSBackend):
    name = "irodori_mlx_stream"

    def __init__(
        self,
        *,
        model: str = "mlx-community/Irodori-TTS-500M-v3-8bit",
        voice: str = "none",
        num_steps: int = 6,
        max_chars: int = 18,
        t_schedule_mode: str = "sway",
        sway_coeff: float = -1.0,
        model_factory: ModelFactory | None = None,
        loaded_model: Any | None = None,
    ) -> None:
        self.model_name = model
        self.voice = voice
        self.num_steps = num_steps
        self.max_chars = max_chars
        self.t_schedule_mode = t_schedule_mode
        self.sway_coeff = sway_coeff
        self._model_factory = model_factory or _load_mlx_audio_model
        self._model = loaded_model

    @classmethod
    def from_spec(cls, spec: BackendSpec) -> IrodoriMLXStreamBackend:
        return cls(
            model=spec.model or "mlx-community/Irodori-TTS-500M-v3-8bit",
            voice=spec.voice or "none",
        )

    async def synthesize(self, tts_input: TTSInput):
        units = split_streaming_units(tts_input.text.strip(), max_chars=self.max_chars)
        if not units:
            return

        last_index = len(units) - 1
        for sequence, unit in enumerate(units):
            audio, sample_rate = await asyncio.to_thread(
                self._generate_unit_audio,
                unit,
                tts_input,
            )
            yield AudioChunkOut(
                data=_wav_bytes_from_audio(audio, sample_rate=sample_rate),
                sequence=sequence,
                is_last=sequence == last_index,
            )

    async def warm_up(self) -> None:
        async for _ in self.synthesize(TTSInput(text="あ。", style="neutral")):
            return

    def _generate_unit_audio(
        self,
        text: str,
        tts_input: TTSInput,
    ) -> tuple[np.ndarray, int]:
        model = self._load_model()
        duration_scale = IrodoriMLXBackend.STYLE_TO_DURATION_SCALE.get(
            tts_input.style,
            1.0,
        )
        results = model.generate(
            text=text,
            ref_audio=_voice_to_ref_audio(tts_input.voice or self.voice),
            seconds=_estimate_seconds(text, duration_scale=duration_scale),
            duration_scale=duration_scale,
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


def split_streaming_units(text: str, *, max_chars: int = 18) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []

    units: list[str] = []
    current: list[str] = []

    for char in stripped:
        current.append(char)
        current_text = "".join(current).strip()
        if not current_text:
            current.clear()
            continue
        if char in _PUNCTUATION:
            units.append(current_text)
            current.clear()
        elif char in _SOFT_BOUNDARY and len(current_text) >= max_chars // 2:
            units.append(current_text)
            current.clear()
        elif len(current_text) >= max_chars:
            units.append(current_text)
            current.clear()

    tail = "".join(current).strip()
    if tail:
        units.append(tail)
    return units


def _estimate_seconds(text: str, *, duration_scale: float) -> float:
    count = len(_COUNTABLE_RE.findall(text))
    estimated = 0.16 + count * 0.085
    return max(0.5, min(3.6, estimated * duration_scale * _DURATION_QUALITY_SCALE))
